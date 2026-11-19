"""Phase 7 failure drills: an outside service goes down; the assistant must degrade
gracefully, alert, never lose or duplicate work, and recover on its own.

Existing drills elsewhere: outbox retry/dead-letter/alert and stuck-item reclaim
(test_db_reliability.py), concurrent bookings (test_db_phase2.py), Jira sync timeouts
(test_db_phase6.py).
"""

import asyncio
import uuid

import httpx
from sqlalchemy import func, select

from assistant import alerts
from assistant.auth import StaffUser, get_current_staff
from assistant.booking import available_slots
from assistant.config_store import DEFAULTS
from assistant.integrations import build_calendar
from assistant.kb.embeddings import HashEmbedder
from assistant.llm.base import LLMError, LLMResponse, ToolCall
from assistant.llm.fake import FakeProvider
from assistant.models import (
    Booking,
    Company,
    Consultant,
    Contact,
    Conversation,
    Message,
    OutboxItem,
    Ticket,
)
from assistant.outbox.worker import OutboxWorker
from assistant.settings import Settings, get_settings
from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db
EMB = HashEmbedder()


def _down(*_a, **_k):
    raise LLMError("503 Service Unavailable; gemini timed out", provider="chain", retryable=True)


def _app(llm=None, internal_llm=None):
    from assistant.api.main import create_app

    return create_app(
        llm=llm or FakeProvider(),
        internal_llm=internal_llm,
        embedder=EMB,
        database_url=TEST_DATABASE_URL,
    )


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_every_model_down_visitor_gets_a_polite_fallback_and_we_are_alerted(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_alert", lambda *a, **k: _record(sent, a, k))
    app = _app(llm=FakeProvider(_down))
    async with app.router.lifespan_context(app), _client(app) as c:
        start = (await c.post("/v1/chat/sessions", json={"consent": True})).json()
        r = await c.post(
            "/v1/chat/messages",
            json={"text": "Do you do cloud migrations?"},
            headers={"Authorization": f"Bearer {start['token']}"},
        )
    body = r.json()
    assert r.status_code == 200 and body["degraded"] is True
    assert "having trouble" in body["reply"] and "consultant" in body["reply"]
    assert sent and sent[0][0] == ("critical", "LLM chain failed")


async def _record(sent, a, k):
    sent.append((a, k))


async def test_local_model_down_internal_assistant_degrades_too(sm):
    app = _app(internal_llm=FakeProvider(_down))
    async with app.router.lifespan_context(app), _client(app) as c:
        app.dependency_overrides[get_current_staff] = lambda: StaffUser(
            "d1", "d1", "d1@acme.example", frozenset({"consultant"})
        )
        cid = (await c.post("/v1/internal/conversations")).json()["conversation_id"]
        r = await c.post(f"/v1/internal/conversations/{cid}/messages", json={"text": "Our SOP?"})
    assert r.status_code == 200 and r.json()["degraded"] is True


class _FlakyJira:
    """Down for the first `down_for` calls, then fine. Remembers what it created."""

    name = "jira"

    def __init__(self, down_for):
        self.down_for, self.calls, self.created = down_for, 0, []

    async def create(self, *, ref, **_):
        self.calls += 1
        if self.calls <= self.down_for:
            raise httpx.ConnectError("Jira unreachable")
        self.created.append(ref)
        return f"SUP-{len(self.created)}"

    async def status(self, external_id):
        return "Open"

    async def comment(self, external_id, text):
        return None


async def test_jira_down_ticket_is_kept_retried_and_created_once_when_it_returns(sm):
    from assistant.outbox.handlers import build_handlers
    from assistant.tools.registry import enqueue

    async with sm() as s:
        co = Company(id=uuid.uuid4(), name="Northbridge")
        s.add(co)
        await s.flush()
        t = Ticket(company_id=co.id, summary="VPN drops", system="jira")
        s.add(t)
        await s.flush()
        await enqueue(s, "ticket.create", {"ticket_id": str(t.id)}, key=f"ticket:{t.id}")
        await s.commit()
    jira = _FlakyJira(down_for=2)
    worker = OutboxWorker(sm, build_handlers(sm, tickets=jira), Settings(_env_file=None))
    for _ in range(2):  # two failed attempts: still pending, ticket still there
        await worker.run_once()
        await _due_now(sm)  # skip the backoff wait
        async with sm() as s:
            item = (await s.execute(select(OutboxItem))).scalar_one()
            assert item.status == "pending" and "unreachable" in (item.last_error or "")
    await worker.run_once()  # Jira is back
    await _due_now(sm)
    await worker.run_once()  # nothing left to do: no second create
    async with sm() as s:
        item = (await s.execute(select(OutboxItem))).scalar_one()
        ticket = (await s.execute(select(Ticket))).scalar_one()
    assert item.status == "done" and ticket.external_id == "SUP-1"
    assert jira.created == [ticket.ref]  # created exactly once


async def test_slack_unreachable_message_waits_and_is_not_lost(sm, monkeypatch):
    from assistant.outbox.handlers import build_handlers
    from assistant.tools.registry import enqueue

    # A closed local port stands in for Slack being down (and keeps tests off the real one).
    monkeypatch.setattr(get_settings(), "slack_notify_webhook_url", "http://127.0.0.1:9/hook")
    async with sm() as s:
        await enqueue(s, "slack.notify", {"text": "Handover H-1 taken"}, key="drill-slack")
        await s.commit()
    worker = OutboxWorker(sm, build_handlers(sm), Settings(_env_file=None))
    await worker.run_once()
    async with sm() as s:
        item = (await s.execute(select(OutboxItem))).scalar_one()
    assert item.status == "pending" and item.attempts == 1 and item.next_attempt_at is not None


async def test_database_down_readiness_fails_liveness_holds():
    from assistant.api.main import create_app

    app = create_app(
        llm=FakeProvider(),
        embedder=EMB,
        database_url="postgresql+asyncpg://assistant:wrong@127.0.0.1:9/nowhere",
    )
    async with app.router.lifespan_context(app), _client(app) as c:
        assert (await c.get("/healthz")).status_code == 200  # the process is alive
        r = await c.get("/readyz")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"  # take it out of rotation


async def test_double_submitted_message_books_once(sm):
    """A visitor double-clicks Send (or the network retries): two identical turns race."""
    async with sm() as s:
        s.add(
            Consultant(
                id=uuid.uuid4(), name="Cora", email="cora@acme.example", skills=["discovery"]
            )
        )
        await s.commit()
    probe = _app()
    async with probe.router.lifespan_context(probe), _client(probe) as c:
        start = (await c.post("/v1/chat/sessions", json={"consent": True})).json()
    conv_id = uuid.UUID(start["conversation_id"])

    async with sm() as s:
        slot = (await available_slots(s, build_calendar(), DEFAULTS["booking.rules"]))[0]
    call = LLMResponse(
        None,
        [
            ToolCall(
                "b1",
                "book_discovery_call",
                {
                    "name": "Jo Bloggs",
                    "email": "jo@example.com",
                    "start": slot.start.isoformat(),
                    "topic": "Cloud migration",
                    "timezone": "Europe/London",
                },
            )
        ],
        "fake",
        "f",
    )

    def script(messages, tools):
        # Book on the first step of a turn, then answer.
        last = messages[-1]
        return call if last.role == "user" else LLMResponse("Booked.", [], "fake", "f")

    app = _app(llm=FakeProvider(script))
    async with app.router.lifespan_context(app), _client(app) as c:
        h = {"Authorization": f"Bearer {start['token']}"}
        body = {"text": "Yes, book that slot please. Jo Bloggs, jo@example.com"}
        r1, r2 = await asyncio.gather(
            c.post("/v1/chat/messages", json=body, headers=h),
            c.post("/v1/chat/messages", json=body, headers=h),
        )
    assert r1.status_code == 200 and r2.status_code == 200
    async with sm() as s:
        bookings = (await s.execute(select(func.count()).select_from(Booking))).scalar_one()
        emails = (
            await s.execute(
                select(func.count()).select_from(OutboxItem).where(OutboxItem.kind == "email.send")
            )
        ).scalar_one()
        msgs = (
            await s.execute(
                select(func.count()).select_from(Message).where(Message.conversation_id == conv_id)
            )
        ).scalar_one()
    assert bookings == 1 and emails == 1  # one booking, one confirmation email
    assert msgs > 0
    assert (await _contacts(sm)) == 1


async def _contacts(sm):
    async with sm() as s:
        return (await s.execute(select(func.count()).select_from(Contact))).scalar_one()


async def test_conversation_survives_a_worker_crash_mid_item(sm):
    """Worker dies while processing: the item is reclaimed and done once (no duplicate)."""
    from datetime import UTC, datetime, timedelta

    from assistant.tools.registry import enqueue

    async with sm() as s:
        conv = Conversation(id=uuid.uuid4(), channel="web")
        s.add(conv)
        await enqueue(s, "slack.notify", {"text": "x"}, key="crash")
        await s.flush()
        item = (await s.execute(select(OutboxItem))).scalar_one()
        item.status, item.locked_at = "processing", datetime.now(UTC) - timedelta(hours=1)
        await s.commit()
    delivered = []

    async def handler(payload, key):
        delivered.append(key)
        return "ok"

    worker = OutboxWorker(sm, {"slack.notify": handler}, Settings(_env_file=None))
    await worker.run_once()
    await worker.run_once()
    assert len(delivered) == 1


async def _due_now(sm):
    from datetime import UTC, datetime

    from sqlalchemy import update

    async with sm() as s:
        await s.execute(update(OutboxItem).values(next_attempt_at=datetime.now(UTC)))
        await s.commit()
