"""Run the eval set against the real LLM chain (free Groq tier by default).

    cd apps/api
    ../../.venv/Scripts/python -m evals.run_evals            # all cases
    ../../.venv/Scripts/python -m evals.run_evals --only book,price-ballpark
    ../../.venv/Scripts/python -m evals.run_evals --dry-run  # plumbing check, fake model

Uses the TEST database (wiped and rebuilt), ingests the approved content with the real
embedder and seeds consultants, so results don't depend on dev data. Writes a JSON report
to evals/results/.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from collections import deque
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from assistant import guardrails
from assistant.auth import StaffUser
from assistant.grounding import check as check_grounding
from assistant.integrations.calendar import LocalCalendar
from assistant.kb import internal as internal_kb
from assistant.kb import public as kb
from assistant.kb.embeddings import build_embedder
from assistant.llm.factory import build_chain
from assistant.llm.fake import FakeProvider
from assistant.models import Base, Booking, Conversation, Handover, Message
from assistant.orchestrator import Orchestrator
from assistant.seed import seed_session
from assistant.settings import get_settings
from assistant.tools import ToolRegistry, build_tools
from evals.cases import CASES, INTERNAL_CASES, Case, InternalCase

RESULTS = Path(__file__).parent / "results"
# Models often use typographic characters ("H‑502", "don’t"); compare on plain ASCII.
_TYPOGRAPHY = str.maketrans(
    {"‐": "-", "‑": "-", "‒": "-", "–": "-", "‘": "'", "’": "'", " ": " ", " ": " "}
)


class TokenPacer:
    """Keeps the run under the free tier's per-minute token limit (8k/model on Groq) by
    waiting before a turn while the last 60 seconds' usage is over budget."""

    def __init__(self, budget_per_minute: int):
        self.budget = budget_per_minute
        self.window: deque[tuple[float, int]] = deque()
        self.seen: dict[uuid.UUID, int] = {}

    def _used(self) -> int:
        now = time.monotonic()
        while self.window and now - self.window[0][0] > 60:
            self.window.popleft()
        return sum(t for _, t in self.window)

    def record(self, tokens: int) -> None:
        if tokens > 0:
            self.window.append((time.monotonic(), tokens))

    async def wait(self) -> None:
        """Sleep exactly until enough of the window has expired to be back under budget."""
        excess = self._used() - self.budget
        if excess <= 0:
            return
        freed = 0
        for ts, tokens in self.window:  # oldest first
            freed += tokens
            if freed >= excess:
                await asyncio.sleep(max(0.0, ts + 60 - time.monotonic()) + 0.5)
                return


# ~one model's worth of headroom, leaving room for a turn's 2-3 calls on top.
PACER = TokenPacer(budget_per_minute=6000)


async def prepare(url: str):
    engine = create_async_engine(url, pool_pre_ping=True)  # long model calls
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    embedder = build_embedder()
    async with sm() as s:
        await kb.ingest_dir(s, embedder, Path(get_settings().kb_content_dir))
        await seed_session(s)  # consultants + two fictional client companies (commits)
    return engine, sm, embedder


def blocks_total() -> float:
    return sum(
        s.value
        for m in guardrails.GUARDRAIL_BLOCKS.collect()
        for s in m.samples
        if s.name.endswith("_total")
    )


async def run_case(sm, orch: Orchestrator, case: Case) -> dict:
    async with sm() as s:
        conv = Conversation(id=uuid.uuid4(), channel="web")
        s.add(conv)
        await s.commit()
    replies: list[str] = []
    degraded = 0
    providers: set[str] = set()
    before = blocks_total()
    started = time.perf_counter()
    for turn in case.turns:
        await PACER.wait()
        async with sm() as s:
            result = await orch.handle_turn(s, conv.id, turn)
            used = (
                await s.execute(
                    select(
                        func.coalesce(func.sum(Message.tokens_in + Message.tokens_out), 0)
                    ).where(Message.conversation_id == conv.id)
                )
            ).scalar_one()
        PACER.record(used - PACER.seen.get(conv.id, 0))
        PACER.seen[conv.id] = used
        replies.append(result.reply)
        degraded += result.degraded
        if result.provider:
            providers.add(result.provider)
    elapsed = time.perf_counter() - started
    blocked = blocks_total() - before

    async with sm() as s:
        tools = set(
            (
                await s.execute(
                    select(Message.tool_name).where(
                        Message.conversation_id == conv.id, Message.role == "tool"
                    )
                )
            ).scalars()
        )
        handovers = set(
            (
                await s.execute(select(Handover.reason).where(Handover.conversation_id == conv.id))
            ).scalars()
        )
        bookings = (
            await s.execute(
                select(func.count()).select_from(Booking).where(Booking.conversation_id == conv.id)
            )
        ).scalar_one()

    failures: list[str] = []
    replies = [r.translate(_TYPOGRAPHY) for r in replies]
    if degraded:  # a fallback "having trouble" reply is not a pass, whatever it says
        failures.append(f"model unavailable on {degraded} turn(s) (rate limit / error)")
    for t in case.tools_called:
        if t not in tools:
            failures.append(f"tool not called: {t}")
    final = replies[-1].lower()
    if case.any_of and not any(a.lower() in final for a in case.any_of):
        failures.append(f"final reply lacks any of {case.any_of}")
    for pat in case.none_of:
        for r in replies:
            if re.search(pat, r, re.I):
                failures.append(f"reply matched forbidden /{pat}/")
    if case.no_guardrail and blocked:
        failures.append(f"guardrail had to replace {int(blocked)} reply(ies)")
    if case.handover_reason and case.handover_reason not in handovers:
        failures.append(f"no {case.handover_reason} handover (got {sorted(handovers)})")
    if case.booking_made and not bookings:
        failures.append("no booking was created")

    return {
        "id": case.id,
        "category": case.category,
        "passed": not failures,
        "failures": failures,
        "tools": sorted(t for t in tools if t),
        "replies": replies,
        "providers": sorted(providers),
        "seconds": round(elapsed, 1),
    }


INTERNAL_USERS = {
    "consultant": StaffUser(
        "eval-consultant",
        "consultant.dev",
        "consultant.dev@example.test",
        frozenset({"consultant"}),
    ),
    "manager": StaffUser(
        "eval-manager", "manager.dev", "manager.dev@example.test", frozenset({"manager"})
    ),
}


async def run_internal_case(sm, orch: Orchestrator, embedder, case: InternalCase) -> dict:
    user = INTERNAL_USERS[case.user]
    async with sm() as s:
        conv = Conversation(id=uuid.uuid4(), channel="internal", staff_sub=user.sub)
        s.add(conv)
        await s.commit()

    given: list[str] = []  # exact source texts the model saw, in citation order

    async def documents(s, conv_id, text):
        note, sources = await internal_kb.retrieve_for_turn(s, embedder, user, conv_id, text)
        given[:] = [src.text for src in sources]
        return note, [src.public() for src in sources]

    started = time.perf_counter()
    async with sm() as s:
        result = await orch.handle_turn(
            s, conv.id, case.question, actor_id=user.sub, roles=set(user.roles), context=documents
        )
    reply = result.reply.translate(_TYPOGRAPHY)
    failures: list[str] = []
    if result.degraded:
        failures.append("model unavailable")
    if case.any_of and not any(a in reply.lower() for a in case.any_of):
        failures.append(f"reply lacks any of {case.any_of}")
    for pat in case.none_of:
        if re.search(pat, reply, re.I):
            failures.append(f"reply matched forbidden /{pat}/")
    if case.needs_sources and not result.sources:
        failures.append("no sources returned")
    grounding = check_grounding(reply, given, case.question)
    if case.grounded and not grounding.ok:
        if grounding.unsupported:
            failures.append(f"unsupported facts: {'; '.join(grounding.unsupported[:3])}")
        if grounding.bad_citations:
            failures.append(f"citations to missing sources: {grounding.bad_citations}")
    if case.min_structure:
        found = [h for h in case.structure if h in reply.lower()]
        if len(found) < case.min_structure:
            failures.append(f"template sections {len(found)}/{case.min_structure}: {found}")
    return {
        "id": case.id,
        "category": case.category,
        "user": case.user,
        "passed": not failures,
        "failures": failures,
        "replies": [result.reply],
        "sources": [f"[{x['n']}] {x['title']}" for x in result.sources],
        "facts_checked": grounding.checked,
        # Facts the model invented that the runtime guard replaced with [TO CONFIRM]. Reported
        # so the guard can't hide how often the raw model makes things up.
        "guard_replaced": result.unverified,
        "inline_citations": len(re.findall(r"\[\d{1,2}\]", reply)),
        "seconds": round(time.perf_counter() - started, 1),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--dry-run", action="store_true", help="fake model; checks plumbing only")
    ap.add_argument(
        "--internal",
        action="store_true",
        help="run the internal knowledge assistant cases on the local model",
    )
    args = ap.parse_args()

    url = os.environ.get("TEST_DATABASE_URL") or get_settings().database_url.replace(
        "/assistant", "/assistant_test"
    )
    pool = INTERNAL_CASES if args.internal else CASES
    cases = [c for c in pool if not args.only or c.id in args.only.split(",")]
    if args.dry_run:
        llm = FakeProvider()
    elif args.internal:
        llm = build_chain(get_settings().internal_llm_chain_list)  # local Ollama by default
    else:
        llm = build_chain()
    if not args.dry_run and not args.internal and get_settings().llm_chain_list == ["fake"]:
        print("LLM_CHAIN is 'fake'. Set GROQ_API_KEY and LLM_CHAIN=groq,gemini in .env first.")
        return 2

    # Deterministic verification codes so scripted conversations can "read their email".
    from assistant import identity

    identity.secrets.randbelow = lambda n: 739214
    engine, sm, embedder = await prepare(url)
    orch = Orchestrator(llm, ToolRegistry(build_tools(embedder, LocalCalendar())), internal_llm=llm)
    if args.internal:
        async with sm() as s:
            await internal_kb.ingest_dir(s, embedder, internal_kb.INTERNAL_DIR)
    print(f"Running {len(cases)} cases with {llm.name} ({llm.model}) on {url.rsplit('/', 1)[-1]}\n")

    results = []
    for case in cases:
        if isinstance(case, InternalCase):
            r = await run_internal_case(sm, orch, embedder, case)
            results.append(r)
            mark = "PASS" if r["passed"] else "FAIL"
            guard = r["guard_replaced"]
            print(
                f"[{mark}] {case.id:<20} {case.category:<12} {case.user:<10} "
                f"{r['seconds']:>5}s {'; '.join(r['failures'])}"
                + (f" [guard replaced: {', '.join(guard)}]" if guard else "")
            )
            continue
        r = await run_case(sm, orch, case)
        if any("model unavailable" in f for f in r["failures"]):
            # Free-tier per-minute token limits, not behaviour: let the window reset, retry once.
            print(f"[....] {case.id:<20} rate-limited, retrying in 30s")
            await asyncio.sleep(30)
            r = await run_case(sm, orch, case)
            r["retried"] = True
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(
            f"[{mark}] {case.id:<20} {case.category:<16} {r['seconds']:>5}s "
            f"{'; '.join(r['failures'])}"
        )
    await engine.dispose()

    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed")
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"evals-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    out.write_text(
        json.dumps(
            {
                "model": llm.name,
                "passed": passed,
                "total": len(results),
                "results": results,
                "cases": [asdict(c) for c in cases],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Report: {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
