"""Idempotency, outbox delivery, and the no-double-booking constraint (needs Postgres)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from assistant.llm.base import ToolCall
from assistant.models import Booking, Consultant, Conversation, Message, OutboxItem, ToolIntent
from assistant.outbox.handlers import PermanentError
from assistant.outbox.worker import OutboxWorker
from assistant.settings import Settings
from assistant.tools import ToolContext, ToolRegistry, default_tools
from tests.conftest import requires_db

pytestmark = requires_db
reg = ToolRegistry(default_tools())
EMAIL = {"to": "a@example.com", "subject": "Hi", "body": "Hello"}


async def new_conversation(sm, channel="internal") -> uuid.UUID:
    async with sm() as s:
        c = Conversation(id=uuid.uuid4(), channel=channel)
        s.add(c)
        await s.flush()
        # send_email only acts on an explicit request naming the recipient (Phase 5 guard).
        s.add(
            Message(conversation_id=c.id, role="user", content=f"Please send it to {EMAIL['to']}")
        )
        await s.commit()
        return c.id


async def call_tool(sm, conv_id, args=EMAIL):
    async with sm() as s:
        out = await reg.execute(
            ToolContext(s, conv_id, "internal"), ToolCall("c", "send_email", dict(args))
        )
        await s.commit()
        return out


async def count(sm, model):
    async with sm() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_retried_tool_call_creates_one_intent_and_one_outbox_item(sm):
    conv = await new_conversation(sm)
    first = await call_tool(sm, conv)
    second = await call_tool(sm, conv)
    assert first["status"] == "queued"
    assert second["duplicate"] is True and second["reference"] == first["reference"]
    assert await count(sm, ToolIntent) == 1
    assert await count(sm, OutboxItem) == 1


async def test_concurrent_identical_calls_are_deduplicated(sm):
    conv = await new_conversation(sm)
    results = await asyncio.gather(*(call_tool(sm, conv) for _ in range(8)))
    assert sum(1 for r in results if r["status"] == "queued") == 1
    assert await count(sm, OutboxItem) == 1


async def test_different_args_are_different_intents(sm):
    conv = await new_conversation(sm)
    await call_tool(sm, conv)
    await call_tool(sm, conv, {**EMAIL, "subject": "Other"})
    assert await count(sm, ToolIntent) == 2


def worker(sm, handlers, max_attempts=3):
    s = Settings(outbox_max_attempts=max_attempts, outbox_stuck_after_s=60, _env_file=None)
    return OutboxWorker(sm, handlers, s)


async def make_ready(sm):
    """Skip backoff waits in tests."""
    async with sm() as s, s.begin():
        await s.execute(update(OutboxItem).values(next_attempt_at=func.now()))


async def test_worker_delivers_once_and_marks_intent_done(sm):
    sent = []

    async def handler(payload, key):
        sent.append((payload["to"], key))
        return "msg-1"

    conv = await new_conversation(sm)
    await call_tool(sm, conv)
    w = worker(sm, {"email.send": handler})
    assert await w.run_once() == 1
    assert await w.run_once() == 0  # nothing left: no double send
    assert len(sent) == 1
    async with sm() as s:
        intent = (await s.execute(select(ToolIntent))).scalar_one()
        assert intent.status == "done" and intent.result == {"external_refs": ["msg-1"]}


async def test_worker_retries_then_dead_letters_and_alerts(sm, monkeypatch):
    alerts_sent = []

    async def fake_alert(severity, title, **d):
        alerts_sent.append(title)
        return True

    monkeypatch.setattr("assistant.outbox.worker.alerts.send_alert", fake_alert)

    async def flaky(payload, key):
        raise ConnectionError("smtp down")

    conv = await new_conversation(sm)
    await call_tool(sm, conv)
    # The retry budget is stored on each item when it's enqueued; the worker honours it.
    async with sm() as s, s.begin():
        await s.execute(update(OutboxItem).values(max_attempts=3))
    w = worker(sm, {"email.send": flaky})
    outcomes = []
    for _ in range(3):
        await make_ready(sm)
        batch = await w.claim()
        outcomes += [await w.process(i) for i in batch]
    assert outcomes == ["retry", "retry", "dead"]
    async with sm() as s:
        item = (await s.execute(select(OutboxItem))).scalar_one()
        intent = (await s.execute(select(ToolIntent))).scalar_one()
    assert item.status == "dead" and item.attempts == 3 and "smtp down" in item.last_error
    assert intent.status == "failed"
    assert alerts_sent and "dead-lettered" in alerts_sent[0]


async def test_permanent_error_dead_letters_immediately(sm):
    async def reject(payload, key):
        raise PermanentError("bad recipient")

    conv = await new_conversation(sm)
    await call_tool(sm, conv)
    batch = await worker(sm, {"email.send": reject}).claim()
    assert await worker(sm, {}).process(batch[0]) == "dead"  # unknown kind also dead


async def test_stuck_processing_items_are_reclaimed(sm):
    conv = await new_conversation(sm)
    await call_tool(sm, conv)
    w = worker(sm, {})
    await w.claim()  # claimed, then "crash" before processing
    async with sm() as s, s.begin():
        await s.execute(
            update(OutboxItem).values(locked_at=datetime.now(UTC) - timedelta(minutes=10))
        )
    assert await w.reclaim_stuck() == 1
    assert len(await w.claim()) == 1


async def test_two_workers_never_claim_the_same_item(sm):
    conv = await new_conversation(sm)
    for i in range(6):
        await call_tool(sm, conv, {**EMAIL, "subject": f"s{i}"})
    w1, w2 = worker(sm, {}), worker(sm, {})
    a, b = await asyncio.gather(w1.claim(), w2.claim())
    ids_a, ids_b = {i.id for i in a}, {i.id for i in b}
    assert not ids_a & ids_b and len(ids_a | ids_b) == 6


async def test_database_rejects_overlapping_bookings(sm):
    async with sm() as s:
        c = Consultant(name="Cora", email="cora@example.com")
        s.add(c)
        await s.commit()
        consultant_id = c.id  # read now: a rollback below expires ORM attributes
        start = datetime(2026, 12, 1, 10, tzinfo=UTC)

        def booking(offset_min, key, status="confirmed"):
            st = start + timedelta(minutes=offset_min)
            return Booking(
                consultant_id=consultant_id,
                starts_at=st,
                ends_at=st + timedelta(minutes=30),
                idempotency_key=key,
                status=status,
            )

        s.add(booking(0, "k1"))
        await s.commit()
        s.add(booking(30, "k2"))  # back-to-back is fine ('[)' range)
        await s.commit()
        s.add(booking(15, "k3"))  # overlaps both
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
        s.add(booking(15, "k4", status="cancelled"))  # cancelled bookings don't block
        await s.commit()
