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
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from assistant import guardrails
from assistant.integrations.calendar import LocalCalendar
from assistant.kb import public as kb
from assistant.kb.embeddings import build_embedder
from assistant.llm.factory import build_chain
from assistant.llm.fake import FakeProvider
from assistant.models import Base, Booking, Consultant, Conversation, Message
from assistant.orchestrator import Orchestrator
from assistant.settings import get_settings
from assistant.tools import ToolRegistry, build_tools
from evals.cases import CASES, Case

RESULTS = Path(__file__).parent / "results"


async def prepare(url: str):
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    embedder = build_embedder()
    async with sm() as s:
        await kb.ingest_dir(s, embedder, Path(get_settings().kb_content_dir))
        s.add_all(
            [
                Consultant(name="Cora Consultant", email="cora@example.com", skills=["discovery"]),
                Consultant(name="Sam Security", email="sam@example.com", skills=["discovery"]),
            ]
        )
        await s.commit()
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
        async with sm() as s:
            result = await orch.handle_turn(s, conv.id, turn)
        replies.append(result.reply)
        degraded += result.degraded
        if result.provider:
            providers.add(result.provider)
        await asyncio.sleep(1.0)  # be gentle with free-tier rate limits
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
        bookings = (
            await s.execute(
                select(func.count()).select_from(Booking).where(Booking.conversation_id == conv.id)
            )
        ).scalar_one()

    failures: list[str] = []
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


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--dry-run", action="store_true", help="fake model; checks plumbing only")
    args = ap.parse_args()

    url = os.environ.get("TEST_DATABASE_URL") or get_settings().database_url.replace(
        "/assistant", "/assistant_test"
    )
    cases = [c for c in CASES if not args.only or c.id in args.only.split(",")]
    llm = FakeProvider() if args.dry_run else build_chain()
    if not args.dry_run and get_settings().llm_chain_list == ["fake"]:
        print("LLM_CHAIN is 'fake'. Set GROQ_API_KEY and LLM_CHAIN=groq,gemini in .env first.")
        return 2

    engine, sm, embedder = await prepare(url)
    orch = Orchestrator(llm, ToolRegistry(build_tools(embedder, LocalCalendar())))
    print(f"Running {len(cases)} cases with {llm.name} on {url.rsplit('/', 1)[-1]}\n")

    results = []
    for case in cases:
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
