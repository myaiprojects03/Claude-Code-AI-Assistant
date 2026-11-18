"""Dashboard read APIs (Phase 6) against Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from assistant.auth import StaffUser, get_current_staff
from assistant.llm.fake import FakeProvider
from assistant.models import (
    AuditLog,
    Booking,
    Company,
    Consultant,
    Contact,
    Conversation,
    Handover,
    Lead,
    Message,
    Ticket,
)
from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db


@pytest.fixture
async def data(sm):
    now = datetime.now(UTC)
    async with sm() as s:
        cons = Consultant(
            id=uuid.uuid4(), name="Cora Consultant", email="cora@example.com", skills=["discovery"]
        )
        co = Company(id=uuid.uuid4(), name="Northbridge Legal")
        s.add_all([cons, co])
        await s.flush()
        ct = Contact(
            id=uuid.uuid4(),
            name="Priya Shah",
            email="priya@example.com",
            company_id=co.id,
            is_client=True,
        )
        s.add(ct)
        await s.flush()
        convs = []
        for i, status in enumerate(["qualified", "unqualified", "new"]):
            c = Conversation(id=uuid.uuid4(), channel="web", contact_id=ct.id if i == 0 else None)
            s.add(c)
            await s.flush()
            s.add(
                Lead(
                    conversation_id=c.id,
                    status=status,
                    contact_id=ct.id if i == 0 else None,
                    need_category="cloud_migration",
                )
            )
            s.add(Message(conversation_id=c.id, role="user", content=f"hello {i}"))
            s.add(
                Message(
                    conversation_id=c.id,
                    role="assistant",
                    content="hi",
                    provider="groq",
                    tokens_in=1000,
                    tokens_out=50,
                )
            )
            convs.append(c)
        s.add(Conversation(id=uuid.uuid4(), channel="internal"))  # never counted as traffic
        s.add(
            Booking(
                consultant_id=cons.id,
                contact_id=ct.id,
                conversation_id=convs[0].id,
                starts_at=now + timedelta(days=2),
                ends_at=now + timedelta(days=2, minutes=30),
                idempotency_key="k1",
                status="confirmed",
                topic="M365",
            )
        )
        s.add(
            Handover(
                conversation_id=convs[1].id,
                reason="complaint_or_legal",
                urgency="high",
                route_to="#x",
                summary="Unhappy",
            )
        )
        s.add(Ticket(company_id=co.id, contact_id=ct.id, summary="Printer", priority="urgent"))
        s.add(Ticket(company_id=co.id, contact_id=ct.id, summary="Old", status="Done"))
        await s.commit()
    return convs


@pytest.fixture
async def client(sm):
    from assistant.api.main import create_app

    app = create_app(llm=FakeProvider(), database_url=TEST_DATABASE_URL)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


def as_staff(client, *roles):
    client.app.dependency_overrides[get_current_staff] = lambda: StaffUser(
        "staff-1", "u", "u@example.com", frozenset(roles)
    )


async def test_overview_numbers(client, data):
    as_staff(client, "consultant")
    o = (await client.get("/v1/dashboard/overview")).json()
    assert o["conversations_7d"] == 3  # the internal one is excluded
    assert o["leads_30d"] == {"qualified": 1, "unqualified": 1, "new": 1}
    assert o["qualified_rate_30d"] == 0.5
    assert o["bookings_upcoming"] == 1 and o["bookings_next_7d"] == 1
    assert o["handovers_open"] == {"high": 1}
    assert o["tickets_open"] == 1
    assert o["llm_tokens_today"] == 3 * 1050 and o["llm_calls_today"] == 3
    assert len(o["daily_conversations"]) == 14 and o["daily_conversations"][-1]["count"] == 3


async def test_role_enforcement(client, data):
    as_staff(client, "consultant")
    for path in ("/v1/dashboard/conversations", "/v1/dashboard/leads", "/v1/dashboard/config"):
        assert (await client.get(path)).status_code == 403
    for path in ("/v1/dashboard/bookings", "/v1/dashboard/tickets", "/v1/dashboard/me"):
        assert (await client.get(path)).status_code == 200
    client.app.dependency_overrides.clear()
    assert (await client.get("/v1/dashboard/overview")).status_code == 401


async def test_lists(client, data, sm):
    as_staff(client, "manager")
    convs = (await client.get("/v1/dashboard/conversations")).json()
    assert len(convs) == 3  # internal chats are private, never listed
    found = (await client.get("/v1/dashboard/conversations", params={"q": "priya"})).json()
    assert len(found) == 1 and found[0]["company"] == "Northbridge Legal"
    assert found[0]["messages"] == 2 and found[0]["last_message"] == "hi"

    leads = (await client.get("/v1/dashboard/leads", params={"status": "qualified"})).json()
    assert [lead["email"] for lead in leads] == ["priya@example.com"]
    async with sm() as s:
        assert (await s.execute(select(AuditLog.action))).scalars().all() == ["leads.list"]

    bookings = (await client.get("/v1/dashboard/bookings")).json()
    assert bookings[0]["consultant"] == "Cora Consultant" and bookings[0]["topic"] == "M365"
    assert (await client.get("/v1/dashboard/bookings", params={"scope": "past"})).json() == []

    tickets = (await client.get("/v1/dashboard/tickets")).json()
    assert [t["summary"] for t in tickets] == ["Printer"]  # closed ones hidden by default
    all_t = (await client.get("/v1/dashboard/tickets", params={"open_only": False})).json()
    assert len(all_t) == 2


async def test_config_catalogue_shows_defaults_then_published(client, data):
    as_staff(client, "admin")
    cat = {c["key"]: c for c in (await client.get("/v1/dashboard/config")).json()}
    assert cat["chat.greeting"]["source"] == "default"
    await client.post("/v1/config/chat.greeting/drafts", json={"value": "Hi there!"})
    cat = {c["key"]: c for c in (await client.get("/v1/dashboard/config")).json()}
    assert cat["chat.greeting"]["draft_versions"] == 1
    await client.post("/v1/config/chat.greeting/versions/1/publish")
    cat = {c["key"]: c for c in (await client.get("/v1/dashboard/config")).json()}
    assert cat["chat.greeting"]["source"] == "published"
    assert cat["chat.greeting"]["value"] == "Hi there!"
    assert cat["chat.greeting"]["published_version"] == 1


async def test_me(client, data):
    as_staff(client, "manager", "consultant")
    assert (await client.get("/v1/dashboard/me")).json()["roles"] == ["consultant", "manager"]
