"""Phase 2 against Postgres: knowledge base, leads, booking, handlers, guardrail in a turn."""

import asyncio
import uuid

import httpx
from sqlalchemy import func, select

from assistant.integrations.calendar import LocalCalendar
from assistant.integrations.crm import LeadSync
from assistant.kb import public as kb
from assistant.kb.embeddings import HashEmbedder
from assistant.llm.base import LLMResponse, ToolCall
from assistant.llm.fake import FakeProvider
from assistant.models import (
    Booking,
    Consultant,
    Contact,
    Conversation,
    Document,
    Lead,
    OutboxItem,
    PublicChunk,
    ToolIntent,
)
from assistant.outbox.handlers import build_handlers
from assistant.outbox.worker import OutboxWorker
from assistant.settings import REPO_ROOT, Settings
from assistant.tools import ToolContext, ToolRegistry, build_tools
from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db
EMB = HashEmbedder()
CAL = LocalCalendar()
reg = ToolRegistry(build_tools(EMB, CAL))
CONTENT = REPO_ROOT / "content" / "public"


async def new_conv(sm) -> uuid.UUID:
    async with sm() as s:
        c = Conversation(id=uuid.uuid4(), channel="web")
        s.add(c)
        await s.commit()
        return c.id


async def call(sm, conv, name, args):
    async with sm() as s:
        out = await reg.execute(ToolContext(s, conv, "web"), ToolCall("c", name, args))
        await s.commit()
        return out


async def count(sm, model, *where):
    async with sm() as s:
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        return (await s.execute(q)).scalar_one()


async def add_consultants(sm, n=1):
    async with sm() as s:
        for i in range(n):
            s.add(
                Consultant(name=f"Con Sultant{i}", email=f"c{i}@example.com", skills=["discovery"])
            )
        await s.commit()


# ---------- Knowledge base ----------


async def test_ingest_is_idempotent_and_search_finds_relevant_section(sm):
    async with sm() as s:
        first = await kb.ingest_dir(s, EMB, CONTENT)
        again = await kb.ingest_dir(s, EMB, CONTENT)
        hits = await kb.search(s, EMB, "Can you migrate our servers to Azure cloud?", min_score=0.0)
    assert first["added"] == 3 and first["chunks"] > 10
    assert again == {"added": 0, "updated": 0, "unchanged": 3, "removed": 0, "chunks": 0}
    assert hits[0].section == "Cloud migration"


async def test_removed_content_is_deleted_from_index(sm, tmp_path):
    (tmp_path / "a.md").write_text("---\ntitle: A\n---\n## One\nAlpha text.\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\ntitle: B\n---\n## Two\nBeta text.\n", encoding="utf-8")
    async with sm() as s:
        await kb.ingest_dir(s, EMB, tmp_path)
        (tmp_path / "b.md").unlink()
        stats = await kb.ingest_dir(s, EMB, tmp_path)
    assert stats["removed"] == 1
    assert await count(sm, Document) == 1
    assert await count(sm, PublicChunk) == 1


async def test_search_tool_says_when_nothing_is_approved(sm):
    out = await call(sm, await new_conv(sm), "search_knowledge", {"query": "pizza recipes"})
    assert out["results"] == [] and "don't know" in out["note"]


# ---------- Leads ----------


async def test_lead_profile_builds_lead_and_syncs_crm_once_per_state(sm):
    conv = await new_conv(sm)
    out = await call(
        sm,
        conv,
        "update_lead_profile",
        {
            "need": "Move file server to cloud",
            "need_category": "cloud_migration",
            "company_size": "51-250",
        },
    )
    assert out["lead_status"] == "new" and "email" in out["missing"]
    assert await count(sm, OutboxItem) == 0  # no email yet -> nothing to sync

    args = {
        "name": "Jo Bloggs",
        "email": "Jo@Example.com",
        "company": "Initech",
        "budget_range": "unknown",
        "timeline": "1-3m",
        "role": "decision_maker",
    }
    out = await call(sm, conv, "update_lead_profile", args)
    assert out["lead_status"] == "qualified" and out["missing"] == []
    await call(sm, conv, "update_lead_profile", {"role": "decision_maker"})  # same state again
    assert await count(sm, OutboxItem, OutboxItem.kind == "crm.sync") == 1

    async with sm() as s:
        lead = (await s.execute(select(Lead))).scalar_one()
        contact = (await s.execute(select(Contact))).scalar_one()
    assert contact.email == "jo@example.com"  # normalised
    assert lead.contact_id == contact.id and "Category: cloud_migration" in lead.summary


# ---------- Booking ----------


async def first_slot(sm, conv) -> str:
    out = await call(sm, conv, "find_available_slots", {"timezone": "Europe/London"})
    return out["slots"][0]["start"]


def booking_args(start, email="jo@example.com"):
    return {
        "start": start,
        "name": "Jo Bloggs",
        "email": email,
        "timezone": "Europe/London",
        "topic": "Cloud migration",
    }


async def test_no_consultants_means_no_slots(sm):
    out = await call(sm, await new_conv(sm), "find_available_slots", {})
    assert out["slots"] == []


async def test_booking_happy_path_and_retry_is_deduplicated(sm):
    await add_consultants(sm)
    conv = await new_conv(sm)
    start = await first_slot(sm, conv)
    out = await call(sm, conv, "book_discovery_call", booking_args(start))
    assert out["status"] == "queued" and out["consultant"] == "Con"
    again = await call(sm, conv, "book_discovery_call", booking_args(start))
    assert again["duplicate"] is True
    assert await count(sm, Booking) == 1
    async with sm() as s:
        kinds = sorted((await s.execute(select(OutboxItem.kind))).scalars())
    immediate = [k for k in kinds if k != "booking.reminder"]
    assert immediate == ["calendar.create_event", "crm.sync", "email.send", "slack.notify"]
    assert 1 <= kinds.count("booking.reminder") <= 2  # consultant 1h; client 24h if still ahead


async def test_taken_slot_is_not_offered_and_cannot_be_booked(sm):
    await add_consultants(sm, n=1)
    a, b = await new_conv(sm), await new_conv(sm)
    start = await first_slot(sm, a)
    await call(sm, a, "book_discovery_call", booking_args(start, "a@example.com"))
    assert await first_slot(sm, b) != start
    out = await call(sm, b, "book_discovery_call", booking_args(start, "b@example.com"))
    assert "just taken" in out["error"]
    # A rejected call leaves nothing behind: no intent, no outbox items for b.
    assert await count(sm, ToolIntent, ToolIntent.conversation_id == b) == 0


async def test_concurrent_bookings_for_the_last_consultant(sm):
    await add_consultants(sm, n=1)
    convs = [await new_conv(sm) for _ in range(5)]
    start = await first_slot(sm, convs[0])
    results = await asyncio.gather(
        *(
            call(sm, c, "book_discovery_call", booking_args(start, f"p{i}@example.com"))
            for i, c in enumerate(convs)
        )
    )
    assert sum(1 for r in results if r.get("status") == "queued") == 1
    assert await count(sm, Booking) == 1


async def test_two_consultants_allow_two_bookings_at_same_time(sm):
    await add_consultants(sm, n=2)
    a, b = await new_conv(sm), await new_conv(sm)
    start = await first_slot(sm, a)
    for conv, email in ((a, "a@example.com"), (b, "b@example.com")):
        assert (await call(sm, conv, "book_discovery_call", booking_args(start, email)))[
            "status"
        ] == "queued"
    async with sm() as s:
        consultants = set((await s.execute(select(Booking.consultant_id))).scalars())
    assert len(consultants) == 2


async def test_model_cannot_book_arbitrary_times(sm):
    await add_consultants(sm)
    conv = await new_conv(sm)
    out = await call(
        sm, conv, "book_discovery_call", booking_args("2026-11-22T03:00:00+00:00")
    )  # a Sunday, 3am
    assert "isn't an available" in out["error"]


async def test_one_booking_per_conversation(sm):
    await add_consultants(sm, n=2)
    conv = await new_conv(sm)
    out = await call(sm, conv, "find_available_slots", {})
    s1, s2 = out["slots"][0]["start"], out["slots"][1]["start"]
    await call(sm, conv, "book_discovery_call", booking_args(s1))
    out = await call(sm, conv, "book_discovery_call", booking_args(s2))
    assert "already booked" in out["error"]


# ---------- Worker handlers ----------


class RecordingCRM:
    name = "rec"

    def __init__(self):
        self.calls: list[LeadSync] = []

    async def upsert_lead(self, lead, key):
        self.calls.append(lead)
        return "hubspot:contact:42"


async def test_worker_confirms_booking_and_syncs_crm(sm):
    await add_consultants(sm)
    conv = await new_conv(sm)
    await call(sm, conv, "book_discovery_call", booking_args(await first_slot(sm, conv)))
    crm, sent = RecordingCRM(), []

    async def fake_email(payload, key):
        sent.append(payload)
        return "m1"

    handlers = build_handlers(sm, crm=crm, calendar=CAL)
    handlers["email.send"] = fake_email
    w = OutboxWorker(sm, handlers, Settings(_env_file=None))
    while await w.run_once():
        pass

    async with sm() as s:
        booking = (await s.execute(select(Booking))).scalar_one()
        contact = (await s.execute(select(Contact))).scalar_one()
        statuses = {
            k: v
            for k, v in (await s.execute(select(OutboxItem.kind, OutboxItem.status))).all()
            if k != "booking.reminder"
        }
    assert booking.status == "confirmed" and booking.calendar_event_id.startswith("local:")
    assert set(statuses.values()) == {"done"}  # reminders stay scheduled for later
    assert crm.calls[0].email == "jo@example.com" and contact.hubspot_id == "42"
    assert "Your discovery call" in sent[0]["subject"] and "Cloud migration" in sent[0]["body"]


# ---------- Guardrail inside a real turn ----------


async def test_guardrail_replaces_a_price_in_a_turn(sm):
    from assistant.api.main import create_app

    llm = FakeProvider([LLMResponse("That would cost about £12,000.", [], "fake", "fake-1")])
    app = create_app(llm=llm, tools=reg, database_url=TEST_DATABASE_URL)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            tok = (await c.post("/v1/chat/sessions", json={"consent": True})).json()["token"]
            r = await c.post(
                "/v1/chat/messages",
                json={"text": "How much for a migration?"},
                headers={"Authorization": f"Bearer {tok}"},
            )
    reply = r.json()["reply"]
    assert "£" not in reply and "discovery call" in reply


async def test_knowledge_tool_used_in_a_turn(sm, monkeypatch):
    from assistant.api.main import create_app
    from assistant.settings import get_settings

    # The offline hash embedder scores lower than the real model; relax the cut-off here.
    monkeypatch.setattr(get_settings(), "kb_min_score", 0.1)

    async with sm() as s:
        await kb.ingest_dir(s, EMB, CONTENT)

    def script(messages, tools):
        last = messages[-1]
        if last.role == "tool":
            assert "Cloud migration" in last.content  # retrieved approved content
            return LLMResponse("Yes, we plan and carry out cloud migrations.", [], "fake", "f")
        return LLMResponse(
            None,
            [ToolCall("k1", "search_knowledge", {"query": "migrate servers to Azure"})],
            "fake",
            "f",
        )

    app = create_app(llm=FakeProvider(script), tools=reg, database_url=TEST_DATABASE_URL)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            tok = (await c.post("/v1/chat/sessions", json={"consent": True})).json()["token"]
            r = await c.post(
                "/v1/chat/messages",
                json={"text": "Do you do Azure migrations?"},
                headers={"Authorization": f"Bearer {tok}"},
            )
    assert r.json()["reply"].startswith("Yes, we plan")
