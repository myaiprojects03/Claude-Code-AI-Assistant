"""Phase 3 against Postgres: verification, client isolation, tickets, bookings, handover."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from assistant import identity
from assistant.auth import StaffUser, get_current_staff
from assistant.integrations.calendar import LocalCalendar
from assistant.kb.embeddings import HashEmbedder
from assistant.llm.base import LLMResponse, ToolCall
from assistant.llm.fake import FakeProvider
from assistant.models import (
    AuditLog,
    Booking,
    Company,
    Consultant,
    Contact,
    Conversation,
    DoNotContact,
    Handover,
    Message,
    OutboxItem,
    Project,
    Ticket,
)
from assistant.outbox.handlers import make_reminder_handler, make_ticket_handlers
from assistant.tools import ToolContext, ToolRegistry, build_tools
from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db
CAL = LocalCalendar()
CODE = "739214"


class FakeJira:
    name = "jira"

    def __init__(self):
        self.created, self.comments = [], []

    async def create(self, *, ref, summary, description, priority, company):
        self.created.append(ref)
        return f"SUP-{len(self.created)}"

    async def status(self, external_id):
        return "In Progress"

    async def comment(self, external_id, text):
        self.comments.append((external_id, text))


JIRA = FakeJira()
reg = ToolRegistry(build_tools(HashEmbedder(), CAL, JIRA))


@pytest.fixture(autouse=True)
def fixed_code(monkeypatch):
    monkeypatch.setattr(identity.secrets, "randbelow", lambda n: int(CODE))


@pytest.fixture
async def clients(sm):
    """Two client companies (one enterprise), a consultant, tickets and projects."""
    async with sm() as s:
        cora = Consultant(
            id=uuid.uuid4(), name="Cora Consultant", email="cora@example.com", skills=["discovery"]
        )
        s.add(cora)
        await s.flush()
        out = {}
        for key, name, enterprise, email in (
            ("nb", "Northbridge Legal", False, "priya@northbridge.example.com"),
            ("hal", "Halcyon Manufacturing", True, "tom@halcyon.example.com"),
        ):
            co = Company(
                id=uuid.uuid4(),
                name=name,
                is_enterprise=enterprise,
                account_manager_id=cora.id if enterprise else None,
            )
            s.add(co)
            await s.flush()
            ct = Contact(
                id=uuid.uuid4(),
                email=email,
                name=f"{key} person",
                is_client=True,
                company_id=co.id,
                timezone="Europe/London",
            )
            s.add(ct)
            await s.flush()
            t = Ticket(
                company_id=co.id,
                contact_id=ct.id,
                summary=f"{name} printer jam",
                status="Open",
                priority="normal",
            )
            s.add(t)
            s.add(
                Project(
                    company_id=co.id,
                    name=f"{name} migration",
                    status="on track",
                    summary="Going well.",
                )
            )
            await s.flush()
            out[key] = {"company": co.id, "contact": ct.id, "email": email, "ticket": t.ref}
        await s.commit()
    return out


async def new_conv(sm) -> uuid.UUID:
    async with sm() as s:
        c = Conversation(id=uuid.uuid4(), channel="web")
        s.add(c)
        await s.commit()
        return c.id


async def say(sm, conv, text):
    """The visitor sends a message."""
    async with sm() as s:
        s.add(Message(conversation_id=conv, role="user", content=text))
        await s.commit()


async def call(sm, conv, name, args=None, *, typed=True):
    if name == "verify_code" and typed:  # the visitor types the code before it's checked
        await say(sm, conv, f"My code is {args['code']}")
    if name == "send_verification_code" and typed:  # ...and their email before a code is sent
        await say(sm, conv, f"My email is {args['email']}")
    async with sm() as s:
        verified = (await s.get(Conversation, conv)).verified  # as the orchestrator does
        ctx = ToolContext(s, conv, "web", verified=verified)
        out = await reg.execute(ctx, ToolCall("c", name, args or {}))
        await s.commit()
        return out


async def verified(sm, email) -> uuid.UUID:
    conv = await new_conv(sm)
    await call(sm, conv, "send_verification_code", {"email": email})
    assert (await call(sm, conv, "verify_code", {"code": CODE}))["verified"] is True
    return conv


async def count(sm, model, *where):
    async with sm() as s:
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        return (await s.execute(q)).scalar_one()


# ---------- Verification ----------


async def test_unknown_and_known_emails_get_identical_replies(sm, clients):
    a, b = await new_conv(sm), await new_conv(sm)
    unknown = await call(sm, a, "send_verification_code", {"email": "nobody@example.com"})
    known = await call(sm, b, "send_verification_code", {"email": clients["nb"]["email"]})
    # No client enumeration: same message and shape; only their own typed address differs.
    assert unknown["note"] == known["note"] and unknown.keys() == known.keys()
    emails = await count(sm, OutboxItem, OutboxItem.kind == "email.send")
    assert emails == 1  # a code is only actually sent to a known address


async def test_code_never_stored_in_plain_text(sm, clients):
    conv = await new_conv(sm)
    await call(sm, conv, "send_verification_code", {"email": clients["nb"]["email"]})
    async with sm() as s:
        from assistant.models import VerificationCode

        row = (await s.execute(select(VerificationCode))).scalar_one()
    assert CODE not in row.code_hash and len(row.code_hash) == 64


async def test_wrong_codes_lock_after_three_attempts(sm, clients):
    conv = await new_conv(sm)
    await call(sm, conv, "send_verification_code", {"email": clients["nb"]["email"]})
    for _ in range(3):
        assert "isn't right" in (await call(sm, conv, "verify_code", {"code": "000000"}))["error"]
    out = await call(sm, conv, "verify_code", {"code": CODE})  # right code, but locked now
    assert "No active code" in out["error"]


async def test_model_cannot_invent_a_code(sm, clients):
    """The code must appear in the visitor's own latest message (eval finding 2026-11-17:
    the model once called verify_code with a guessed code before the visitor typed one)."""
    conv = await new_conv(sm)
    await call(sm, conv, "send_verification_code", {"email": clients["nb"]["email"]})
    await say(sm, conv, "ok, checking my inbox")
    out = await call(sm, conv, "verify_code", {"code": CODE}, typed=False)
    assert "latest message" in out["error"]
    async with sm() as s:
        from assistant.models import VerificationCode

        row = (await s.execute(select(VerificationCode))).scalar_one()
        assert row.attempts == 0  # a refused guess doesn't use up the visitor's attempts
        assert not (await s.get(Conversation, conv)).verified
    # Once the visitor types it (spaces are fine), it works.
    await say(sm, conv, "it's 739 214")
    out = await call(sm, conv, "verify_code", {"code": CODE}, typed=False)
    assert out["verified"] is True


async def test_model_typo_in_email_is_corrected_from_what_the_visitor_typed(sm, clients):
    """Eval finding 2026-11-17: the model sent the code to 'priyaa.shah@...'."""
    conv = await new_conv(sm)
    await say(sm, conv, f"Hi, I'm {clients['nb']['email']}, I need my ticket status")
    out = await call(
        sm, conv, "send_verification_code", {"email": "priyaa@northbridge.example.com"}, typed=False
    )
    assert out["email_used"] == clients["nb"]["email"] and "correction" in out
    assert await count(sm, OutboxItem, OutboxItem.kind == "email.send") == 1  # code really sent


async def test_send_code_needs_an_email_the_visitor_typed(sm, clients):
    conv = await new_conv(sm)
    await say(sm, conv, "I'm an existing client")
    out = await call(
        sm, conv, "send_verification_code", {"email": clients["nb"]["email"]}, typed=False
    )
    assert "type the email" in out["error"]


async def test_expired_code_rejected(sm, clients):
    conv = await new_conv(sm)
    await call(sm, conv, "send_verification_code", {"email": clients["nb"]["email"]})
    async with sm() as s:
        from assistant.models import VerificationCode

        row = (await s.execute(select(VerificationCode))).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
    assert "No active code" in (await call(sm, conv, "verify_code", {"code": CODE}))["error"]


async def test_code_requests_are_rate_limited(sm, clients):
    conv = await new_conv(sm)
    for _ in range(3):
        await call(sm, conv, "send_verification_code", {"email": clients["nb"]["email"]})
    out = await call(sm, conv, "send_verification_code", {"email": clients["nb"]["email"]})
    assert "Too many" in out["error"]


async def test_client_tools_require_verification(sm, clients):
    conv = await new_conv(sm)
    for tool in ("list_my_tickets", "get_project_updates", "list_my_bookings"):
        assert "Verify the visitor first" in (await call(sm, conv, tool))["error"]


# ---------- Isolation between clients ----------


async def test_clients_only_see_their_own_tickets_and_projects(sm, clients):
    conv = await verified(sm, clients["nb"]["email"])
    tickets = (await call(sm, conv, "list_my_tickets"))["tickets"]
    assert [t["summary"] for t in tickets] == ["Northbridge Legal printer jam"]
    projects = (await call(sm, conv, "get_project_updates"))["projects"]
    assert [p["name"] for p in projects] == ["Northbridge Legal migration"]

    # Another client's real ticket number looks exactly like a non-existent one.
    other = await call(sm, conv, "get_ticket", {"ref": clients["hal"]["ticket"]})
    missing = await call(sm, conv, "get_ticket", {"ref": "T-99999"})
    assert "No ticket" in other["error"] and "No ticket" in missing["error"]
    upd = await call(
        sm, conv, "add_ticket_update", {"ref": clients["hal"]["ticket"], "update": "sneaky"}
    )
    assert "No ticket" in upd["error"]


async def test_verified_non_client_gets_no_client_data(sm, clients):
    async with sm() as s:
        s.add(Contact(email="lead@example.com", name="Just A Lead"))
        await s.commit()
    conv = await verified(sm, "lead@example.com")
    assert "don't have client records" in (await call(sm, conv, "list_my_tickets"))["error"]


# ---------- Tickets ----------


async def test_create_ticket_and_mirror_to_jira(sm, clients):
    conv = await verified(sm, clients["nb"]["email"])
    out = await call(
        sm,
        conv,
        "create_ticket",
        {
            "summary": "Shared drive is read-only",
            "description": "Since 9am nobody can save.",
            "priority": "normal",
        },
    )
    assert out["status"] == "queued" and out["ticket"].startswith("T-")
    assert "escalated" not in out
    assert await count(sm, OutboxItem, OutboxItem.kind == "ticket.create") == 1
    handlers = make_ticket_handlers(sm, JIRA)
    async with sm() as s:
        t = (
            await s.execute(select(Ticket).where(Ticket.summary == "Shared drive is read-only"))
        ).scalar_one()
    assert await handlers["ticket.create"]({"ticket_id": str(t.id)}, "k") == "SUP-1"
    assert await handlers["ticket.create"]({"ticket_id": str(t.id)}, "k") == "SUP-1"  # retry
    assert JIRA.created.count(t.ref) == 1

    await call(sm, conv, "add_ticket_update", {"ref": t.ref, "update": "It's the S: drive."})
    assert await handlers["ticket.comment"]({"ticket_id": str(t.id), "text": "S: drive"}, "k")
    status = await call(sm, conv, "get_ticket", {"ref": t.ref})
    assert status["status"] == "In Progress"  # live from the (fake) Jira
    assert status["latest_updates"][-1]["text"] == "It's the S: drive."


async def test_urgent_ticket_escalates_to_a_person_without_ending_chat(sm, clients):
    conv = await verified(sm, clients["nb"]["email"])
    out = await call(
        sm,
        conv,
        "create_ticket",
        {
            "summary": "Whole office offline",
            "description": "No internet at all since 8am.",
            "priority": "urgent",
        },
    )
    assert out["escalated"].startswith("H-")
    async with sm() as s:
        h = (await s.execute(select(Handover))).scalar_one()
        c = await s.get(Conversation, conv)
    assert h.reason == "technical_issue" and h.urgency == "high"
    assert c.status == "active"


# ---------- Handover ----------


async def test_request_handover_steps_aside_and_dedupes(sm, clients):
    conv = await new_conv(sm)
    out = await call(
        sm,
        conv,
        "request_handover",
        {"reason": "pricing_negotiation", "summary": "Wants a discount on a 3-year deal."},
    )
    assert out["handover"].startswith("H-") and out["handover"] in out["tell_visitor"]
    again = await call(
        sm,
        conv,
        "request_handover",
        {"reason": "pricing_negotiation", "summary": "Wants a discount on a 3-year deal."},
    )
    assert again["handover"] == out["handover"]
    async with sm() as s:
        c = await s.get(Conversation, conv)
        kinds = sorted((await s.execute(select(OutboxItem.kind))).scalars())
    assert c.status == "handed_over"
    assert kinds == ["email.send", "slack.notify"]  # once, not twice


async def test_enterprise_handover_goes_to_account_manager(sm, clients):
    conv = await verified(sm, clients["hal"]["email"])
    await call(
        sm,
        conv,
        "request_handover",
        {"reason": "complaint_or_legal", "summary": "Unhappy with project delays."},
    )
    async with sm() as s:
        h = (await s.execute(select(Handover))).scalar_one()
        email = (
            await s.execute(
                select(OutboxItem.payload).where(
                    OutboxItem.kind == "email.send",
                    OutboxItem.payload["to"].astext == "cora@example.com",
                )
            )
        ).scalar_one_or_none()
    assert "cora@example.com" in h.route_to and email is not None
    assert "Verified client: yes" in email["body"] and "(enterprise)" in email["body"]


async def turn_app(llm):
    from assistant.api.main import create_app

    return create_app(llm=llm, tools=reg, database_url=TEST_DATABASE_URL)


async def chat(app, texts):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            tok = (await c.post("/v1/chat/sessions", json={"consent": True})).json()["token"]
            h = {"Authorization": f"Bearer {tok}"}
            return [
                (await c.post("/v1/chat/messages", json={"text": t}, headers=h)).json()
                for t in texts
            ]


async def test_security_keywords_page_on_call_before_the_model_runs(sm, clients):
    seen = []

    def script(messages, tools):
        seen.append(messages[-1].content)
        return LLMResponse("We're treating this as urgent.", [], "fake", "f")

    replies = await chat(
        await turn_app(FakeProvider(script)), ["Our files are encrypted, I think it's ransomware!"]
    )
    assert replies[0]["reply"] == "We're treating this as urgent."
    assert "SECURITY INCIDENT" in seen[0] and "H-" in seen[0]
    async with sm() as s:
        h = (await s.execute(select(Handover))).scalar_one()
        conv = await s.get(Conversation, h.conversation_id)
    assert (h.reason, h.urgency) == ("security_incident", "p1")
    assert conv.status == "active"  # keeps going to collect contact details


async def test_after_handover_the_assistant_steps_aside(sm, clients):
    calls = {"n": 0}

    def script(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(
                None,
                [
                    ToolCall(
                        "h",
                        "request_handover",
                        {"reason": "human_requested", "summary": "Visitor asked for a person."},
                    )
                ],
                "fake",
                "f",
            )
        return LLMResponse("A consultant will be in touch.", [], "fake", "f")

    replies = await chat(
        await turn_app(FakeProvider(script)),
        ["Can I talk to a human please?", "Hello? Are you there?"],
    )
    assert replies[0]["reply"] == "A consultant will be in touch."
    assert "consultant is now looking after this conversation" in replies[1]["reply"]
    assert calls["n"] == 2  # the model wasn't called for the second message


async def test_staff_can_list_and_acknowledge_handovers(sm, clients):
    conv = await new_conv(sm)
    await call(sm, conv, "request_handover", {"reason": "sensitive", "summary": "Personal matter."})
    app = await turn_app(FakeProvider())
    app.dependency_overrides[get_current_staff] = lambda: StaffUser(
        "cons-1", "c", None, frozenset({"consultant"})
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            open_ = (await c.get("/v1/handovers")).json()
            r = await c.post(f"/v1/handovers/{open_[0]['id']}/acknowledge")
            still_open = (await c.get("/v1/handovers")).json()
    assert r.json()["status"] == "acknowledged" and r.json()["acknowledged_by"] == "cons-1"
    assert still_open == []
    assert await count(sm, AuditLog, AuditLog.action == "handover.acknowledge") == 1


# ---------- Bookings: reschedule, cancel, reminders ----------


async def book(sm, conv, email):
    slots = (await call(sm, conv, "find_available_slots", {}))["slots"]
    out = await call(
        sm,
        conv,
        "book_discovery_call",
        {
            "start": slots[0]["start"],
            "name": "Priya Shah",
            "email": email,
            "timezone": "Europe/London",
            "topic": "Teams rollout",
        },
    )
    assert out["status"] == "queued"
    return slots


async def test_reschedule_and_cancel_flow_with_reminders(sm, clients):
    email = clients["nb"]["email"]
    conv = await new_conv(sm)
    slots = await book(sm, conv, email)

    vconv = await verified(sm, email)  # later, in a new chat
    mine = (await call(sm, vconv, "list_my_bookings"))["bookings"]
    assert len(mine) == 1
    out = await call(
        sm,
        vconv,
        "reschedule_booking",
        {"booking_id": mine[0]["booking_id"], "new_start": slots[1]["start"]},
    )
    assert out["status"] == "queued"

    async with sm() as s:
        rows = (await s.execute(select(Booking).order_by(Booking.created_at))).scalars().all()
    old, new = rows
    assert old.status == "cancelled" and new.status == "pending"

    remind = make_reminder_handler(sm)
    stale = {
        "booking_id": str(old.id),
        "starts_at": old.starts_at.isoformat(),
        "audience": "client",
    }
    assert (await remind(stale, "k")).startswith("skipped")  # old reminders don't fire

    mine = (await call(sm, vconv, "list_my_bookings"))["bookings"]
    out = await call(sm, vconv, "cancel_booking", {"booking_id": mine[0]["booking_id"]})
    assert out["status"] == "queued"
    assert await count(sm, Booking, Booking.status != "cancelled") == 0


async def test_cannot_touch_someone_elses_booking(sm, clients):
    conv = await new_conv(sm)
    await book(sm, conv, clients["nb"]["email"])
    async with sm() as s:
        bid = str((await s.execute(select(Booking.id))).scalar_one())[:8]
    other = await verified(sm, clients["hal"]["email"])
    out = await call(sm, other, "cancel_booking", {"booking_id": bid})
    assert "No upcoming booking" in out["error"]


async def test_client_reminder_respects_do_not_contact(sm, clients, monkeypatch):
    conv = await new_conv(sm)
    await book(sm, conv, clients["nb"]["email"])
    async with sm() as s:
        b = (await s.execute(select(Booking))).scalar_one()
        s.add(DoNotContact(channel="email", address=clients["nb"]["email"], reason="asked"))
        await s.commit()
    sent = []

    async def fake_send(payload, key):
        sent.append(payload)
        return "m"

    monkeypatch.setattr("assistant.outbox.handlers.send_email", fake_send)
    payload = {
        "booking_id": str(b.id),
        "starts_at": b.starts_at.isoformat(),
        "audience": "client",
        "timezone": "Europe/London",
    }
    assert await make_reminder_handler(sm)(payload, "k") == "skipped: do-not-contact"
    assert sent == []


async def test_client_tools_are_hidden_and_blocked_until_verified(sm, clients):
    names = {t.name for t in reg.specs_for("web", verified=False)}
    assert "send_verification_code" in names and "list_my_tickets" not in names
    assert "list_my_tickets" in {t.name for t in reg.specs_for("web", verified=True)}

    conv = await new_conv(sm)
    async with sm() as s:  # even if the model calls it anyway, it's refused
        out = await reg.execute(
            ToolContext(s, conv, "web", verified=False), ToolCall("c", "list_my_tickets", {})
        )
    assert "Verify the visitor first" in out["error"]


async def test_send_code_echoes_the_address_used(sm, clients):
    out = await call(
        sm,
        await new_conv(sm),
        "send_verification_code",
        {"email": "Priyaa.Shah@northbridge.example.com"},
    )
    assert out["email_used"] == "priyaa.shah@northbridge.example.com"
