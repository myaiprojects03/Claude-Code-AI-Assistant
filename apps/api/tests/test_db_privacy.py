"""Phase 7: GDPR export, erasure, retention, and the audit log viewer."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from assistant.auth import StaffUser, get_current_staff
from assistant.kb.embeddings import HashEmbedder
from assistant.llm.fake import FakeProvider
from assistant.models import (
    AuditLog,
    Booking,
    Consent,
    Consultant,
    Contact,
    Conversation,
    DoNotContact,
    Handover,
    Lead,
    Message,
    OutboxItem,
    VerificationCode,
)
from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db
EMB = HashEmbedder()


async def _person(s, email, name, *, booking_in_days=2):
    cons = (await s.execute(select(Consultant).limit(1))).scalar_one_or_none()
    if cons is None:
        cons = Consultant(
            id=uuid.uuid4(),
            name="Cora",
            email=f"cora{uuid.uuid4().hex[:4]}@acme.example",
            skills=[],
        )
        s.add(cons)
        await s.flush()
    c = Contact(id=uuid.uuid4(), name=name, email=email, phone="+44 20 7946 0000")
    s.add(c)
    await s.flush()
    conv = Conversation(id=uuid.uuid4(), channel="web", contact_id=c.id)
    s.add(conv)
    await s.flush()
    s.add_all(
        [
            Message(
                conversation_id=conv.id, role="user", content=f"Hi, I'm {name}, call me on 07700"
            ),
            Message(conversation_id=conv.id, role="assistant", content="Thanks, noted."),
            Consent(
                conversation_id=conv.id, kind="chat_processing", granted=True, text_version="v1"
            ),
            Lead(conversation_id=conv.id, contact_id=c.id, status="new", need=f"{name} needs help"),
            Handover(
                conversation_id=conv.id,
                contact_id=c.id,
                reason="human_requested",
                urgency="normal",
                route_to="#x / y@acme.example",
                summary=f"{name} asked for a person",
            ),
        ]
    )
    start = datetime.now(UTC) + timedelta(days=booking_in_days)
    s.add(
        Booking(
            consultant_id=cons.id,
            contact_id=c.id,
            conversation_id=conv.id,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            idempotency_key=uuid.uuid4().hex,
            status="confirmed",
            topic=f"Call with {name}",
            calendar_event_id="local:abc",
        )
    )
    s.add(
        OutboxItem(
            idempotency_key=uuid.uuid4().hex,
            kind="email.send",
            payload={"to": email, "subject": "Reminder", "body": f"Hi {name}"},
        )
    )
    await s.flush()
    return c, conv


def _app():
    from assistant.api.main import create_app

    return create_app(llm=FakeProvider(), embedder=EMB, database_url=TEST_DATABASE_URL)


async def _admin_client(app, role="admin"):
    app.dependency_overrides[get_current_staff] = lambda: StaffUser(
        f"{role}-1", role, f"{role}@acme.example", frozenset({role}), role.title()
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_export_has_their_data_and_nobody_elses(sm):
    async with sm() as s:
        await _person(s, "alex@bob.example.com", "Alex Bob")
        await _person(s, "jo@other.example.com", "Jo Other", booking_in_days=4)
        await s.commit()
    app = _app()
    async with app.router.lifespan_context(app), await _admin_client(app) as c:
        summary = (
            await c.get("/v1/dashboard/privacy", params={"email": "ALEX@bob.example.com"})
        ).json()
        assert summary["found"] and summary["conversations"] == 1 and summary["leads"] == 1
        assert summary["retention"]["conversation_days"] == 365
        r = await c.get("/v1/dashboard/privacy/export", params={"email": "alex@bob.example.com"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["contact"]["name"] == "Alex Bob"
    assert any("call me on 07700" in m["content"] for m in data["messages"])
    assert data["leads"][0]["need"] == "Alex Bob needs help"
    assert data["consents"][0]["kind"] == "chat_processing"
    assert "Jo Other" not in r.text
    async with sm() as s:
        actions = (await s.execute(select(AuditLog.action, AuditLog.target_id))).all()
    assert {"privacy.lookup", "privacy.export"} <= {a for a, _ in actions}
    assert all("alex" not in (t or "") for _, t in actions)  # hashed, not the address


async def test_erasure_removes_them_and_leaves_others(sm):
    async with sm() as s:
        alex, conv = await _person(s, "alex@bob.example.com", "Alex Bob")
        await _person(s, "jo@other.example.com", "Jo Other", booking_in_days=4)
        await s.commit()
    app = _app()
    async with app.router.lifespan_context(app), await _admin_client(app) as c:
        r = await c.post(
            "/v1/dashboard/privacy/erase",
            json={"email": "alex@bob.example.com", "confirm": "wrong"},
        )
        assert r.status_code == 422
        r = await c.post(
            "/v1/dashboard/privacy/erase",
            json={"email": "alex@bob.example.com", "confirm": "Alex@Bob.example.com"},
        )
        assert r.status_code == 200
        stats = r.json()
        after = (
            await c.get("/v1/dashboard/privacy", params={"email": "alex@bob.example.com"})
        ).json()
    assert stats["future_bookings_cancelled"] == 1 and stats["queued_messages_stopped"] == 1
    assert not after["found"] and after["on_do_not_contact"]
    async with sm() as s:
        contact = await s.get(Contact, alex.id)
        assert contact.name is None and contact.email is None and contact.phone is None
        texts = (
            (await s.execute(select(Message.content).where(Message.conversation_id == conv.id)))
            .scalars()
            .all()
        )
        assert texts and all("erased" in t for t in texts)
        lead = (await s.execute(select(Lead).where(Lead.contact_id == alex.id))).scalar_one()
        assert lead.need is None and lead.status == "new"  # stats kept, words gone
        booking = (
            await s.execute(select(Booking).where(Booking.contact_id == alex.id))
        ).scalar_one()
        assert booking.status == "cancelled" and booking.topic is None
        h = (await s.execute(select(Handover).where(Handover.contact_id == alex.id))).scalar_one()
        assert "Alex" not in h.summary
        kinds = (await s.execute(select(OutboxItem.kind, OutboxItem.status))).all()
        assert ("calendar.cancel_event", "pending") in kinds
        assert (await s.execute(select(DoNotContact.address))).scalars().all() == [
            "alex@bob.example.com"
        ]
        # Jo is untouched.
        jo = (await s.execute(select(Contact).where(Contact.name == "Jo Other"))).scalar_one()
        assert jo.email == "jo@other.example.com"
        erase = (
            await s.execute(select(AuditLog).where(AuditLog.action == "privacy.erase"))
        ).scalar_one()
        assert erase.details["messages"] == 2 and "alex" not in str(erase.details)


async def test_privacy_and_audit_are_admin_only():
    app = _app()
    async with app.router.lifespan_context(app):
        for role in ("manager", "consultant"):
            async with await _admin_client(app, role) as c:
                assert (
                    await c.get("/v1/dashboard/privacy", params={"email": "a@b.co"})
                ).status_code == 403
                assert (await c.get("/v1/dashboard/audit")).status_code == 403


async def test_retention_expires_old_data_but_keeps_what_is_still_open(sm):
    from assistant import privacy

    policy = {
        "conversation_days": 365,
        "internal_chat_days": 180,
        "code_days": 7,
        "outbox_days": 90,
        "audit_days": 730,
    }
    now = datetime.now(UTC)
    old = now - timedelta(days=400)
    async with sm() as s:
        stale = Conversation(id=uuid.uuid4(), channel="web", created_at=old, updated_at=old)
        open_case = Conversation(id=uuid.uuid4(), channel="web", created_at=old, updated_at=old)
        recent = Conversation(id=uuid.uuid4(), channel="web")
        s.add_all([stale, open_case, recent])
        await s.flush()
        for cv in (stale, open_case, recent):
            s.add(Message(conversation_id=cv.id, role="user", content="my phone is 07700 900000"))
        s.add(
            Handover(
                conversation_id=open_case.id,
                reason="complaint_or_legal",
                urgency="high",
                route_to="#x / y@acme.example",
                summary="still open",
            )
        )
        s.add(
            VerificationCode(
                conversation_id=recent.id,
                email="a@b.co",
                code_hash="x" * 64,
                expires_at=now - timedelta(days=30),
            )
        )
        s.add(AuditLog(actor_type="system", action="ancient", at=now - timedelta(days=800)))
        await s.commit()
    async with sm() as s:
        stats = await privacy.apply_retention(s, policy, now)
        await s.commit()
    assert stats["messages_erased"] == 1 and stats["verification_codes_deleted"] == 1
    assert stats["audit_rows_deleted"] == 1
    async with sm() as s:

        def text_of(cid):
            return select(Message.content).where(Message.conversation_id == cid)

        assert (await s.execute(text_of(stale.id))).scalar_one().startswith("[removed")
        assert "07700" in (await s.execute(text_of(open_case.id))).scalar_one()  # open handover
        assert "07700" in (await s.execute(text_of(recent.id))).scalar_one()
    async with sm() as s:  # running again changes nothing
        again = await privacy.apply_retention(s, policy, now)
    assert not any(again.values())


async def test_worker_runs_retention_and_audits_it(sm):
    from assistant.outbox.worker import OutboxWorker

    async with sm() as s:
        old = datetime.now(UTC) - timedelta(days=400)
        cv = Conversation(id=uuid.uuid4(), channel="web", created_at=old, updated_at=old)
        s.add(cv)
        await s.flush()
        s.add(Message(conversation_id=cv.id, role="user", content="old"))
        await s.commit()
    stats = await OutboxWorker(sessionmaker=sm, handlers={}).run_retention()
    assert stats["messages_erased"] == 1
    async with sm() as s:
        row = (
            await s.execute(select(AuditLog).where(AuditLog.action == "privacy.retention"))
        ).scalar_one()
    assert row.actor_id == "retention"


async def test_audit_log_viewer_filters_and_pages(sm):
    async with sm() as s:
        for i in range(5):
            s.add(
                AuditLog(
                    actor_type="staff", actor_id="u1", action="config.publish", target_id=f"k{i}"
                )
            )
        s.add(
            AuditLog(actor_type="staff", actor_id="u2", action="conversation.view", target_id="c1")
        )
        await s.commit()
    app = _app()
    async with app.router.lifespan_context(app), await _admin_client(app) as c:
        rows = (await c.get("/v1/dashboard/audit", params={"action": "config."})).json()
        assert len(rows) == 5 and rows[0]["id"] > rows[-1]["id"]  # newest first
        page2 = (
            await c.get("/v1/dashboard/audit", params={"limit": 2, "before_id": rows[1]["id"]})
        ).json()
        assert [r["id"] for r in page2] == [rows[2]["id"], rows[3]["id"]]
        assert (await c.get("/v1/dashboard/audit", params={"actor": "u2"})).json()[0][
            "action"
        ] == "conversation.view"


async def test_audit_entries_name_the_staff_member(sm):
    app = _app()
    async with app.router.lifespan_context(app), await _admin_client(app) as c:
        await c.get("/v1/dashboard/privacy", params={"email": "nobody@nowhere.example.com"})
        rows = (await c.get("/v1/dashboard/audit", params={"action": "privacy."})).json()
    assert rows[0]["actor_id"] == "admin-1" and rows[0]["details"]["actor_name"] == "Admin"
