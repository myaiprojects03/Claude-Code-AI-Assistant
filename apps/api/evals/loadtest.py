"""Load test (Phase 7): many visitors and staff at once against the real app and Postgres.

The model is a fixed stand-in (FakeProvider), so this measures *our* code and database, not
Groq or Ollama, whose speed and quotas are separate limits (see docs/phase-7). Runs
in-process (no network), on the test database:

    TEST_DATABASE_URL=... python -m evals.loadtest [--visitors 200] [--concurrency 40]
"""

import argparse
import asyncio
import os
import statistics
import time
import uuid

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from assistant.auth import StaffUser, get_current_staff
from assistant.kb.embeddings import HashEmbedder
from assistant.llm.base import LLMResponse
from assistant.llm.fake import FakeProvider
from assistant.models import Base, Conversation, Message
from assistant.settings import get_settings

QUESTIONS = [
    "What services do you offer?",
    "Do you help with Microsoft 365?",
    "What are your office hours?",
]


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0.0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visitors", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=40)
    ap.add_argument("--staff", type=int, default=20, help="dashboard page loads")
    args = ap.parse_args()

    url = os.environ["TEST_DATABASE_URL"]
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        await conn.run_sync(Base.metadata.create_all)
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    s = get_settings()
    s.rate_limit_per_minute = 10**6  # capacity test; rate limiting has its own tests
    s.slack_notify_webhook_url = s.slack_alert_webhook_url = ""

    from assistant.api.main import create_app

    llm = FakeProvider(lambda m, t: LLMResponse("Happy to help with that.", [], "fake", "f"))
    app = create_app(llm=llm, embedder=HashEmbedder(), database_url=url)
    latencies: dict[str, list[float]] = {"start": [], "message": [], "dashboard": []}
    errors: list[str] = []
    gate = asyncio.Semaphore(args.concurrency)

    async def timed(kind: str, coro):
        t = time.perf_counter()
        r = await coro
        latencies[kind].append((time.perf_counter() - t) * 1000)
        if r.status_code >= 400:
            errors.append(f"{kind}:{r.status_code}")
        return r

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://load") as c:

            async def visitor(i: int):
                async with gate:
                    r = await timed("start", c.post("/v1/chat/sessions", json={"consent": True}))
                    if r.status_code != 201:
                        return
                    h = {"Authorization": f"Bearer {r.json()['token']}"}
                    for q in QUESTIONS:
                        await timed(
                            "message", c.post("/v1/chat/messages", json={"text": q}, headers=h)
                        )

            async def staff(i: int):
                async with gate:
                    for path in ("/v1/dashboard/overview", "/v1/handovers", "/v1/dashboard/leads"):
                        await timed("dashboard", c.get(path))

            app.dependency_overrides[get_current_staff] = lambda: StaffUser(
                "load", "load", "load@acme.example", frozenset({"admin"})
            )
            started = time.perf_counter()
            await asyncio.gather(
                *(visitor(i) for i in range(args.visitors)), *(staff(i) for i in range(args.staff))
            )
            wall = time.perf_counter() - started

    sm = async_sessionmaker(eng)
    async with sm() as session:
        convs = (await session.execute(select(func.count()).select_from(Conversation))).scalar_one()
        msgs = (await session.execute(select(func.count()).select_from(Message))).scalar_one()
    await eng.dispose()

    total = sum(len(v) for v in latencies.values())
    print(
        f"{args.visitors} visitors x {len(QUESTIONS)} messages + {args.staff} staff, "
        f"concurrency {args.concurrency}: {total} requests in {wall:.1f}s "
        f"({total / wall:.0f} req/s), errors: {len(errors)}"
    )
    for kind, xs in latencies.items():
        if xs:
            print(
                f"  {kind:<10} n={len(xs):<5} p50={statistics.median(xs):6.0f} ms  "
                f"p95={pct(xs, 95):6.0f} ms  max={max(xs):6.0f} ms"
            )
    expected_msgs = args.visitors * len(QUESTIONS) * 2  # visitor + assistant per turn
    print(
        f"  stored: {convs} conversations, {msgs} messages (expected {args.visitors} and "
        f"{expected_msgs})"
    )
    ok = not errors and convs == args.visitors and msgs == expected_msgs
    print("RESULT:", "PASS" if ok else "FAIL", f"run {uuid.uuid4().hex[:6]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
