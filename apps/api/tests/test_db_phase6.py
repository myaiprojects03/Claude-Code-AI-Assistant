"""Phase 6 additions against Postgres: public content editing, analytics, client access,
internal chat history and privacy of internal chats."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from assistant.auth import StaffUser, get_current_staff
from assistant.kb import public as kb
from assistant.kb.embeddings import HashEmbedder
from assistant.llm.base import LLMResponse
from assistant.llm.fake import FakeProvider
from assistant.models import (
    AuditLog,
    Booking,
    ClientAccess,
    Company,
    Consultant,
    Contact,
    Conversation,
    Handover,
    Lead,
    Message,
)
from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db
EMB = HashEmbedder()


@pytest.fixture
async def client(sm):
    from assistant.api.main import create_app

    app = create_app(llm=FakeProvider(), embedder=EMB, database_url=TEST_DATABASE_URL)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


def as_staff(client, *roles, sub="staff-1", email="u@example.com"):
    client.app.dependency_overrides[get_current_staff] = lambda: StaffUser(
        sub, "u", email, frozenset(roles)
    )


# ---------- Public content ----------


async def test_edit_a_faq_and_the_assistant_finds_it_once_published(client, sm):
    as_staff(client, "admin")
    async with sm() as s:
        await kb.ingest_dir(s, EMB, kb.Path(kb.get_settings().kb_content_dir))
    pages = {p["slug"]: p for p in (await client.get("/v1/dashboard/content")).json()}
    assert {"faq", "services", "about"} <= set(pages)
    faq = pages["faq"]
    assert faq["origin"] == "file" and faq["indexed_chunks"] > 0

    new = faq["raw"] + "\n## Do you support Linux servers?\nYes, we support Ubuntu and RHEL.\n"
    r = await client.post(f"/v1/config/{faq['key']}/drafts", json={"value": new})
    assert r.status_code == 201
    # A draft isn't live: the assistant can't find it yet.
    ask = {"question": "Do you support Linux servers Ubuntu RHEL?"}
    hits = (await client.post("/v1/dashboard/content/ask", json=ask)).json()
    assert not any("Ubuntu" in h["text"] for h in hits)

    r = await client.post(f"/v1/config/{faq['key']}/versions/{r.json()['version']}/publish")
    assert r.status_code == 200
    hits = (await client.post("/v1/dashboard/content/ask", json=ask)).json()
    assert any("Ubuntu" in h["text"] for h in hits)  # live straight away, no developer
    pages = {p["slug"]: p for p in (await client.get("/v1/dashboard/content")).json()}
    assert pages["faq"]["origin"] == "dashboard" and pages["faq"]["published_version"] == 1

    # The CLI ingest keeps the dashboard version instead of reverting to the file.
    async with sm() as s:
        await kb.ingest_dir(s, EMB, kb.Path(kb.get_settings().kb_content_dir))
    hits = (await client.post("/v1/dashboard/content/ask", json=ask)).json()
    assert any("Ubuntu" in h["text"] for h in hits)
    async with sm() as s:
        actions = (await s.execute(select(AuditLog.action))).scalars().all()
    assert {"config.draft", "config.publish", "content.reindex"} <= set(actions)


@pytest.mark.parametrize(
    "text",
    [
        "## Pricing\nA review costs £2,500.",
        "## Rates\nOur consultants charge 900 per day.",
        "## Promise\nWe guarantee zero downtime.",
        "## Timing\nThe migration will be done within 2 weeks.",
    ],
)
async def test_content_with_prices_or_promises_cannot_be_saved(client, text):
    as_staff(client, "admin")
    r = await client.post("/v1/config/content.public.faq/drafts", json={"value": text})
    assert r.status_code == 422 and "can't include prices" in r.json()["detail"]
    preview = (await client.post("/v1/dashboard/content/preview", json={"raw": text})).json()
    assert preview["issues"]


async def test_new_page_and_bad_page_names(client):
    as_staff(client, "admin")
    body = {"value": "---\ntitle: Accessibility\n---\n## Our approach\nWe follow WCAG."}
    assert (
        await client.post("/v1/config/content.public.Bad Name/drafts", json=body)
    ).status_code == 422
    assert (
        await client.post("/v1/config/content.public.accessibility/drafts", json=body)
    ).status_code == 201
    pages = {p["slug"]: p for p in (await client.get("/v1/dashboard/content")).json()}
    assert pages["accessibility"]["origin"] == "draft"
    await client.post("/v1/config/content.public.accessibility/versions/1/publish")
    pages = {p["slug"]: p for p in (await client.get("/v1/dashboard/content")).json()}
    assert pages["accessibility"]["title"] == "Accessibility"
    assert pages["accessibility"]["indexed_chunks"] == 1


async def test_content_roles(client):
    as_staff(client, "consultant")
    assert (await client.get("/v1/dashboard/content")).status_code == 403
    as_staff(client, "manager")
    assert (await client.get("/v1/dashboard/content")).status_code == 200
    r = await client.post("/v1/config/content.public.faq/drafts", json={"value": "## Hi\nHello"})
    assert r.status_code == 403  # publishing and drafting need admin


# ---------- Analytics ----------


async def _conv(s, *, question=None, found=True):
    c = Conversation(id=uuid.uuid4(), channel="web")
    s.add(c)
    await s.flush()
    if question:
        s.add(Message(conversation_id=c.id, role="user", content=question))
        await s.flush()
        result = {"results": [] if not found else [{"source": "x", "text": "y"}], "note": "n"}
        s.add(
            Message(
                conversation_id=c.id,
                role="tool",
                tool_name="search_knowledge",
                tool_call_id="t1",
                content=json.dumps(result),
            )
        )
    return c


async def test_unanswered_topics_group_similar_questions(client, sm):
    async with sm() as s:
        for q in [
            "Do you offer SAP consulting?",
            "do you offer SAP consulting",
            "Can you do Salesforce development?",
        ]:
            await _conv(s, question=q, found=False)
        await _conv(s, question="What is a discovery call?", found=True)
        internal = Conversation(id=uuid.uuid4(), channel="internal")
        s.add(internal)
        await s.commit()
    as_staff(client, "manager")
    topics = (await client.get("/v1/dashboard/unanswered")).json()
    assert [t["count"] for t in topics] == [2, 1]
    assert "sap" in topics[0]["topic"].lower()
    assert all("discovery" not in t["topic"].lower() for t in topics)
    o = (await client.get("/v1/dashboard/overview")).json()
    assert o["unanswered_30d"] == 3
    as_staff(client, "consultant")
    assert (await client.get("/v1/dashboard/unanswered")).status_code == 403


async def test_booking_conversion_and_escalation_rate(client, sm):
    async with sm() as s:
        cons = Consultant(id=uuid.uuid4(), name="C", email="c@example.com", skills=["discovery"])
        ct = Contact(id=uuid.uuid4(), name="P", email="p@example.com")
        s.add_all([cons, ct])
        convs = [await _conv(s) for _ in range(4)]
        for i, c in enumerate(convs[:2]):  # two qualified leads, one of them booked
            s.add(
                Lead(conversation_id=c.id, status="qualified", contact_id=ct.id if i == 0 else None)
            )
        s.add(Lead(conversation_id=convs[2].id, status="unqualified"))
        await s.flush()
        from datetime import UTC, datetime, timedelta

        start = datetime.now(UTC) + timedelta(days=3)
        s.add(
            Booking(
                consultant_id=cons.id,
                contact_id=ct.id,
                conversation_id=convs[0].id,
                starts_at=start,
                ends_at=start + timedelta(minutes=30),
                idempotency_key="k",
                status="confirmed",
            )
        )
        s.add(
            Handover(
                conversation_id=convs[3].id,
                reason="complaint_or_legal",
                urgency="high",
                route_to="#x",
                summary="s",
            )
        )
        await s.commit()
    as_staff(client, "consultant")
    o = (await client.get("/v1/dashboard/overview")).json()
    assert o["qualified_leads_30d"] == 2 and o["booking_conversion_30d"] == 0.5
    assert o["conversations_30d"] == 4 and o["escalation_rate_30d"] == 0.25


# ---------- Client access ----------


async def test_admin_grants_and_revokes_client_access(client, sm):
    async with sm() as s:
        co = Company(id=uuid.uuid4(), name="Halcyon", domain="halcyon.example.com")
        s.add(co)
        await s.commit()
    as_staff(client, "manager")
    assert (await client.get("/v1/dashboard/client-access")).status_code == 403
    as_staff(client, "admin")
    data = (await client.get("/v1/dashboard/client-access")).json()
    assert data["grants"] == [] and data["companies"][0]["name"] == "Halcyon"
    body = {"staff_email": "Consultant.Dev@Example.test", "company_id": str(co.id)}
    r = await client.post("/v1/dashboard/client-access", json=body)
    assert r.status_code == 201 and r.json()["staff_email"] == "consultant.dev@example.test"
    assert (await client.post("/v1/dashboard/client-access", json=body)).status_code == 409

    # The grant takes effect in the internal assistant's access filter.
    from assistant.kb.internal import access_for

    consultant = StaffUser("c", "c", "consultant.dev@example.test", frozenset({"consultant"}))
    async with sm() as s:
        assert co.id in (await access_for(s, consultant)).client_ids

    assert (await client.delete(f"/v1/dashboard/client-access/{r.json()['id']}")).status_code == 204
    async with sm() as s:
        assert (await s.execute(select(ClientAccess))).scalars().all() == []
        actions = (await s.execute(select(AuditLog.action))).scalars().all()
    assert {"client_access.grant", "client_access.revoke"} <= set(actions)


# ---------- Internal chats ----------


async def test_internal_chat_history_is_private_to_its_author(client):
    reply = LLMResponse("Use the onboarding SOP [1].", [], "fake", "f")
    client.app.state.orchestrator.internal_llm = FakeProvider([reply])
    as_staff(client, "consultant", sub="alice")
    cid = (await client.post("/v1/internal/conversations")).json()["conversation_id"]
    await client.post(
        f"/v1/internal/conversations/{cid}/messages", json={"text": "How do we onboard a client?"}
    )
    chats = (await client.get("/v1/internal/conversations")).json()
    assert [c["title"] for c in chats] == ["How do we onboard a client?"]
    turns = (await client.get(f"/v1/internal/conversations/{cid}")).json()
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["content"] == "Use the onboarding SOP [1]."
    assert isinstance(turns[1]["sources"], list)

    as_staff(client, "consultant", sub="bob")
    assert (await client.get("/v1/internal/conversations")).json() == []
    assert (await client.get(f"/v1/internal/conversations/{cid}")).status_code == 404
    # Managers can't read staff members' internal chats from the conversations views either.
    as_staff(client, "admin", sub="carol")
    assert (await client.get(f"/v1/conversations/{cid}")).status_code == 404
    listed = (await client.get("/v1/dashboard/conversations")).json()
    assert cid not in {c["id"] for c in listed}


# ---------- Handover ownership ----------


async def _handover(sm, *, assigned_to=None, urgency="normal"):
    async with sm() as s:
        c = await _conv(s)
        h = Handover(
            conversation_id=c.id,
            reason="complaint_or_legal",
            urgency=urgency,
            route_to="#handover / x",
            summary="Unhappy",
            assigned_to=assigned_to,
        )
        s.add(h)
        await s.commit()
        return str(h.id)


def as_person(client, sub, email, *roles, name=None):
    client.app.dependency_overrides[get_current_staff] = lambda: StaffUser(
        sub, sub, email, frozenset(roles), name
    )


async def test_consultants_take_the_team_queue_or_their_own_not_others(client, sm):
    team = await _handover(sm)
    mine = await _handover(sm, assigned_to="cora@example.test")
    theirs = await _handover(sm, assigned_to="ama@example.test")
    as_person(client, "cora", "Cora@Example.test", "consultant", name="Cora Consultant")
    rows = {h["id"]: h for h in (await client.get("/v1/handovers")).json()}
    assert rows[team]["can_take"] and rows[mine]["can_take"]
    assert not rows[theirs]["can_take"] and not rows[theirs]["can_reassign"]

    r = await client.post(f"/v1/handovers/{theirs}/acknowledge")
    assert r.status_code == 403 and "ama@example.test" in r.json()["detail"]
    r = await client.post(f"/v1/handovers/{mine}/acknowledge")
    assert r.json()["status"] == "acknowledged"
    assert r.json()["acknowledged_by_name"] == "Cora Consultant"  # a name, not an id
    assert (await client.post(f"/v1/handovers/{team}/acknowledge")).status_code == 200

    # Consultants can't reassign.
    r = await client.post(f"/v1/handovers/{theirs}/reassign", json={"to": "cora@example.test"})
    assert r.status_code == 403


async def test_first_person_to_take_it_keeps_it(client, sm):
    hid = await _handover(sm)
    as_person(client, "a", "a@example.test", "consultant", name="A")
    await client.post(f"/v1/handovers/{hid}/acknowledge")
    as_person(client, "b", "b@example.test", "consultant", name="B")
    r = await client.post(f"/v1/handovers/{hid}/acknowledge")
    assert r.json()["acknowledged_by_name"] == "A"
    # B can't close A's handover; A can.
    assert (await client.post(f"/v1/handovers/{hid}/resolve")).status_code == 403
    as_person(client, "a", "a@example.test", "consultant", name="A")
    r = await client.post(f"/v1/handovers/{hid}/resolve")
    assert r.json()["status"] == "resolved" and r.json()["resolved_by_name"] == "A"
    assert not r.json()["can_resolve"] and not r.json()["can_reassign"]


async def test_managers_take_any_and_reassign(client, sm):
    from assistant.models import OutboxItem

    hid = await _handover(sm, assigned_to="ama@example.test", urgency="high")
    as_person(client, "m", "m@example.test", "manager", name="Max Manager")
    row = (await client.get("/v1/handovers")).json()[0]
    assert row["can_take"] and row["can_reassign"]
    await client.post(f"/v1/handovers/{hid}/acknowledge")

    # Reassigning puts it back to open for the new person and tells the team in Slack.
    r = await client.post(f"/v1/handovers/{hid}/reassign", json={"to": "Cora@Example.test"})
    body = r.json()
    assert body["status"] == "open" and body["assigned_to"] == "cora@example.test"
    assert body["acknowledged_by_name"] is None
    as_person(client, "cora", "cora@example.test", "consultant", name="Cora")
    assert (await client.post(f"/v1/handovers/{hid}/acknowledge")).status_code == 200

    # Back to the team queue.
    as_person(client, "m", "m@example.test", "manager", name="Max Manager")
    r = await client.post(f"/v1/handovers/{hid}/reassign", json={"to": None})
    assert r.json()["assigned_to"] is None and r.json()["status"] == "open"
    assert (await client.post(f"/v1/handovers/{hid}/resolve")).json()["status"] == "resolved"
    r = await client.post(f"/v1/handovers/{hid}/reassign", json={"to": "x@example.test"})
    assert r.status_code == 403  # resolved handovers stay closed

    async with sm() as s:
        notes = (
            (await s.execute(select(OutboxItem.payload).where(OutboxItem.kind == "slack.notify")))
            .scalars()
            .all()
        )
        actions = (await s.execute(select(AuditLog.action))).scalars().all()
    assert sum("reassigned to cora@example.test by Max Manager" in n["text"] for n in notes) == 1
    assert {"handover.acknowledge", "handover.reassign", "handover.resolve"} <= set(actions)


async def test_assignees_list_is_for_managers(client, sm):
    async with sm() as s:
        s.add(Consultant(id=uuid.uuid4(), name="Cora", email="Cora@Example.test", skills=[]))
        await s.commit()
    as_person(client, "c", "c@example.test", "consultant")
    assert (await client.get("/v1/handovers/assignees")).status_code == 403
    as_person(client, "m", "m@example.test", "manager")
    assert (await client.get("/v1/handovers/assignees")).json() == [
        {"name": "Cora", "email": "cora@example.test"}
    ]


# ---------- Handover follow-ups: Slack notes and the assistant coming back ----------


async def _slack_texts(sm):
    from assistant.models import OutboxItem

    async with sm() as s:
        rows = await s.execute(
            select(OutboxItem.payload)
            .where(OutboxItem.kind == "slack.notify")
            .order_by(OutboxItem.created_at)
        )
        return [p["text"] for p in rows.scalars()]


async def test_take_and_resolve_are_posted_to_slack_once(client, sm):
    hid = await _handover(sm)
    as_person(client, "cora", "cora@example.test", "consultant", name="Cora Consultant")
    await client.post(f"/v1/handovers/{hid}/acknowledge")
    await client.post(f"/v1/handovers/{hid}/acknowledge")  # a second click changes nothing
    await client.post(f"/v1/handovers/{hid}/resolve")
    texts = await _slack_texts(sm)
    assert sum("taken by Cora Consultant" in t for t in texts) == 1
    assert sum("resolved by Cora Consultant" in t for t in texts) == 1


async def test_assistant_answers_again_once_every_handover_is_resolved(client, sm):
    from assistant.models import ConversationStatus
    from assistant.orchestrator import HANDED_OVER_REPLY

    async with sm() as s:
        conv = Conversation(id=uuid.uuid4(), channel="web", status="handed_over")
        s.add(conv)
        await s.flush()
        ids = []
        for reason in ("complaint_or_legal", "pricing_negotiation"):
            h = Handover(
                conversation_id=conv.id,
                reason=reason,
                urgency="normal",
                route_to="#handover / x",
                summary="s",
            )
            s.add(h)
            await s.flush()
            ids.append(str(h.id))
        await s.commit()
    orch = client.app.state.orchestrator

    async def say(text):
        async with sm() as s:
            return (await orch.handle_turn(s, conv.id, text)).reply

    assert await say("Hello?") == HANDED_OVER_REPLY
    as_person(client, "m", "m@example.test", "manager", name="Max")
    await client.post(f"/v1/handovers/{ids[0]}/resolve")
    assert await say("Still there?") == HANDED_OVER_REPLY  # the other one is still open
    await client.post(f"/v1/handovers/{ids[1]}/resolve")
    async with sm() as s:
        assert (await s.get(Conversation, conv.id)).status == ConversationStatus.active
    assert (await say("Thanks, one more question")).startswith("(fake)")
    texts = await _slack_texts(sm)
    assert sum("answering this conversation again" in t for t in texts) == 1


# ---------- Search ----------


async def test_leads_and_bookings_text_search(client, sm):
    from datetime import UTC, datetime, timedelta

    async with sm() as s:
        cons = Consultant(id=uuid.uuid4(), name="Cora Consultant", email="c@x.test", skills=[])
        co = Company(id=uuid.uuid4(), name="Brightwater Housing")
        s.add_all([cons, co])
        await s.flush()
        ct = Contact(id=uuid.uuid4(), name="Grace Adeyemi", email="grace@bw.test", company_id=co.id)
        other = Contact(id=uuid.uuid4(), name="Jo Bloggs", email="jo@initech.test")
        s.add_all([ct, other])
        await s.flush()
        for contact, need in (
            (ct, "Managed IT for 300 staff"),
            (other, "Move servers to 50%_Azure"),
        ):
            c = await _conv(s)
            s.add(Lead(conversation_id=c.id, contact_id=contact.id, status="qualified", need=need))
            start = datetime.now(UTC) + timedelta(days=2, hours=len(need))
            s.add(
                Booking(
                    consultant_id=cons.id,
                    contact_id=contact.id,
                    conversation_id=c.id,
                    starts_at=start,
                    ends_at=start + timedelta(minutes=30),
                    idempotency_key=uuid.uuid4().hex,
                    status="confirmed",
                    topic="Managed IT" if contact is ct else "Cloud migration",
                )
            )
        await s.commit()
    as_staff(client, "manager")

    async def leads(q):
        rows = (await client.get("/v1/dashboard/leads", params={"q": q})).json()
        return [r["name"] for r in rows]

    async def bookings(q):
        rows = (await client.get("/v1/dashboard/bookings", params={"q": q})).json()
        return [r["contact_name"] for r in rows]

    assert await leads("brightwater") == ["Grace Adeyemi"]  # company
    assert await leads("GRACE@") == ["Grace Adeyemi"]  # email, any case
    assert await leads("300 staff") == ["Grace Adeyemi"]  # need
    assert await leads("50%_") == ["Jo Bloggs"]  # % and _ are literal, not wildcards
    assert await leads("nobody") == []
    assert await bookings("cloud") == ["Jo Bloggs"]  # topic
    assert sorted(await bookings("cora")) == ["Grace Adeyemi", "Jo Bloggs"]  # consultant


# ---------- Rule editors ----------


async def test_rule_settings_are_validated_on_save(client):
    from assistant.config_store import DEFAULTS

    as_staff(client, "admin")
    bad = {**DEFAULTS["office.hours"], "end": "08:00"}
    r = await client.post("/v1/config/office.hours/drafts", json={"value": bad})
    assert r.status_code == 422 and "end time must be after" in r.json()["detail"]
    bad = {**DEFAULTS["qualification.rules"], "min_sizes": ["huge"]}
    r = await client.post("/v1/config/qualification.rules/drafts", json={"value": bad})
    assert r.status_code == 422 and "huge" in r.json()["detail"]
    r = await client.post("/v1/config/handover.rules/drafts", json={"value": "not rules"})
    assert r.status_code == 422
    good = {**DEFAULTS["booking.rules"], "duration_min": 45}
    r = await client.post("/v1/config/booking.rules/drafts", json={"value": good})
    assert r.status_code == 201 and r.json()["value"]["duration_min"] == 45


async def test_rule_previews(client):
    from assistant.config_store import DEFAULTS

    as_staff(client, "manager")

    async def preview(key, value, sample=None):
        r = await client.post(
            f"/v1/dashboard/rules/{key}/preview", json={"value": value, "sample": sample or {}}
        )
        return r.json()

    q = DEFAULTS["qualification.rules"]
    visitor = {
        "need_category": "managed_it",
        "company_size": "11-50",
        "budget_range": "10-50k",
        "timeline": "1-3m",
        "role": "decision_maker",
    }
    assert (await preview("qualification.rules", q, visitor))["result"]["status"] == "qualified"
    stricter = {**q, "min_sizes": ["51-250", "251-1000", "1000+"]}
    res = (await preview("qualification.rules", stricter, visitor))["result"]
    assert res["status"] == "unqualified" and res["reasons"]

    h = DEFAULTS["handover.rules"]
    res = (await preview("handover.rules", h, {"text": "We have been hacked"}))["result"]
    assert res["trigger"] == "security_incident" and res["route"]["urgency"] == "p1"
    res = (await preview("handover.rules", h, {"text": "breach of contract"}))["result"]
    assert res["trigger"] is None

    res = (await preview("booking.rules", DEFAULTS["booking.rules"]))["result"]
    assert 0 < len(res["slots"]) <= DEFAULTS["booking.rules"]["max_slots"]
    res = await preview("office.hours", {**DEFAULTS["office.hours"], "end": "08:00"})
    assert not res["ok"] and "end time" in res["error"]
    r = await client.post("/v1/dashboard/rules/chat.greeting/preview", json={"value": {}})
    assert r.status_code == 404
    as_staff(client, "consultant")
    assert (await client.get("/v1/dashboard/rules/options")).status_code == 403


# ---------- Knowledge assistant: live records ----------


async def _alex(sm):
    """A visitor with a conversation, a security handover, a lead and a call, plus a
    client company with an open ticket."""
    from datetime import UTC, datetime, timedelta

    from assistant.models import Ticket

    async with sm() as s:
        cons = Consultant(id=uuid.uuid4(), name="Cora Consultant", email="c@x.test", skills=[])
        co = Company(id=uuid.uuid4(), name="Halcyon Manufacturing Group", domain="halcyon.test")
        s.add_all([cons, co])
        await s.flush()
        alex = Contact(id=uuid.uuid4(), name="Alex Bob", email="alex@bob.test")
        tom = Contact(
            id=uuid.uuid4(), name="Tom Okafor", email="tom@halcyon.test", company_id=co.id
        )
        s.add_all([alex, tom])
        await s.flush()
        conv = Conversation(id=uuid.uuid4(), channel="web", contact_id=alex.id)
        s.add(conv)
        await s.flush()
        s.add(Message(conversation_id=conv.id, role="user", content="SECRET-TRANSCRIPT-LINE"))
        h = Handover(
            conversation_id=conv.id,
            contact_id=alex.id,
            reason="security_incident",
            urgency="p1",
            route_to="#security-urgent / sec@x.test",
            summary="Visitor reported: our data has been breached",
        )
        s.add(h)
        s.add(
            Lead(
                conversation_id=conv.id,
                contact_id=alex.id,
                status="new",
                need="data breach",
                need_category="cybersecurity",
            )
        )
        start = datetime.now(UTC) + timedelta(days=1)
        s.add(
            Booking(
                consultant_id=cons.id,
                contact_id=alex.id,
                conversation_id=conv.id,
                starts_at=start,
                ends_at=start + timedelta(minutes=30),
                idempotency_key=uuid.uuid4().hex,
                status="confirmed",
                topic="Breach investigation",
            )
        )
        t = Ticket(
            company_id=co.id, contact_id=tom.id, summary="VPN drops at Leeds", priority="high"
        )
        s.add(t)
        await s.commit()
        return h.ref, t.ref


ADMIN = StaffUser("a", "admin.dev", "admin@x.test", frozenset({"admin"}))
CONSULTANT_U = StaffUser("c", "consultant.dev", "consultant@x.test", frozenset({"consultant"}))


async def _ask(sm, user, question):
    from assistant.kb import internal as ik

    async with sm() as s:
        note, srcs = await ik.retrieve_for_turn(s, EMB, user, uuid.uuid4(), question)
        await s.commit()
    return note, srcs


async def test_status_question_about_a_person_gets_their_records(sm):
    href, _ = await _alex(sm)
    note, srcs = await _ask(sm, ADMIN, "What is the status on Alex Bob's query?")
    recs = [x for x in srcs if x.public().get("kind") == "record"]
    assert [r.title for r in recs] == ["Records: Alex Bob"]
    assert f"Handover {href}: security incident, urgency P1, status open" in note
    assert "waiting in the team queue" in note
    assert "Lead status: new" in note and "need: cybersecurity" in note  # managers see leads
    assert "Breach investigation" in note
    assert "SECRET-TRANSCRIPT-LINE" not in note  # never the transcript
    assert recs[0].n == len(srcs)  # numbered after any documents


async def test_consultants_get_records_without_lead_details(sm):
    await _alex(sm)
    note, _ = await _ask(sm, CONSULTANT_U, "any update on alex bob?")
    assert "security incident" in note
    assert "only managers and admins" in note and "need: cybersecurity" not in note


async def test_references_and_clients_are_looked_up(sm):
    href, tref = await _alex(sm)
    note, srcs = await _ask(sm, ADMIN, f"What's happening with {href} and {tref}?")
    titles = [x.title for x in srcs if x.public().get("kind") == "record"]
    assert titles == [f"Handover {href}", f"Ticket {tref}"]
    note, _ = await _ask(sm, ADMIN, "Anything open for Halcyon?")
    assert "VPN drops at Leeds" in note and "Status Open" in note


async def test_no_records_unless_the_question_names_them(sm):
    await _alex(sm)
    note, srcs = await _ask(sm, ADMIN, "Summarise our security incident procedure")
    assert "LIVE RECORDS" not in note
    assert not [x for x in srcs if x.public().get("kind") == "record"]
    # A first name alone isn't enough to pull someone's records.
    note, _ = await _ask(sm, ADMIN, "What did Alex say?")
    assert "LIVE RECORDS" not in note


async def test_record_lookups_are_audited(sm):
    await _alex(sm)
    await _ask(sm, ADMIN, "status of Alex Bob")
    async with sm() as s:
        row = (
            await s.execute(select(AuditLog).where(AuditLog.action == "internal_kb.retrieve"))
        ).scalar_one()
    assert row.details["records"] == ["Records: Alex Bob"]


async def test_consultants_never_get_records_of_clients_they_are_not_assigned(sm):
    from assistant.models import ClientAccess

    href, tref = await _alex(sm)
    async with sm() as s:
        tom = (await s.execute(select(Contact).where(Contact.name == "Tom Okafor"))).scalar_one()
        conv = Conversation(id=uuid.uuid4(), channel="web", contact_id=tom.id)
        s.add(conv)
        await s.flush()
        h = Handover(
            conversation_id=conv.id,
            contact_id=tom.id,
            reason="complaint_or_legal",
            urgency="high",
            route_to="#x / y@x.test",
            summary="Halcyon complaint",
        )
        s.add(h)
        await s.commit()
        tom_ref, halcyon = h.ref, tom.company_id
    for q in (
        "Anything open for Halcyon?",
        "status of Tom Okafor",
        f"what about {tref} and {tom_ref}?",
    ):
        note, _ = await _ask(sm, CONSULTANT_U, q)
        assert "VPN drops" not in note and "Halcyon complaint" not in note, q
    # Alex Bob has no client company, so a consultant still sees his records.
    assert "security incident" in (await _ask(sm, CONSULTANT_U, "status of Alex Bob"))[0]
    # Once assigned to Halcyon, they do see it.
    async with sm() as s:
        s.add(ClientAccess(staff_email="consultant@x.test", company_id=halcyon, granted_by="t"))
        await s.commit()
    assert "VPN drops" in (await _ask(sm, CONSULTANT_U, "Anything open for Halcyon?"))[0]


async def test_call_times_show_the_visitors_own_timezone_too(sm):
    await _alex(sm)
    async with sm() as s:
        alex = (await s.execute(select(Contact).where(Contact.name == "Alex Bob"))).scalar_one()
        alex.timezone = "Asia/Singapore"
        await s.commit()
    note, _ = await _ask(sm, ADMIN, "status of Alex Bob")
    line = next(x for x in note.splitlines() if x.startswith("Discovery call"))
    assert "London time" in line and "for the visitor, Asia/Singapore" in line


# ---------- Tickets page: live statuses from Jira ----------


class _FakeJira:
    name = "jira"

    def __init__(self, statuses, fail=()):
        self.statuses, self.fail, self.calls = statuses, set(fail), []

    async def status(self, external_id):
        self.calls.append(external_id)
        if external_id in self.fail:
            raise RuntimeError("Jira is down")
        return self.statuses.get(external_id)

    async def create(self, **_):
        return None

    async def comment(self, external_id, text):
        return None


async def _ticket_app(adapter):
    from assistant.api.main import create_app

    return create_app(
        llm=FakeProvider(), embedder=EMB, ticket_adapter=adapter, database_url=TEST_DATABASE_URL
    )


async def _tickets(app, **params):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            app.dependency_overrides[get_current_staff] = lambda: StaffUser(
                "s", "s", "s@x.test", frozenset({"consultant"})
            )
            return (await c.get("/v1/dashboard/tickets", params=params)).json()


async def _three_tickets(sm):
    from assistant.models import Ticket

    async with sm() as s:
        co = Company(id=uuid.uuid4(), name="Northbridge Legal")
        s.add(co)
        await s.flush()
        s.add_all(
            [
                Ticket(
                    company_id=co.id, summary="Closed in Jira", system="jira", external_id="SUP-1"
                ),
                Ticket(company_id=co.id, summary="Still open", system="jira", external_id="SUP-2"),
                Ticket(company_id=co.id, summary="Local one"),
            ]
        )
        await s.commit()


async def test_tickets_page_picks_up_status_changes_made_in_jira(sm):
    from assistant.models import Ticket

    await _three_tickets(sm)
    jira = _FakeJira({"SUP-1": "Done", "SUP-2": "In Progress"})
    rows = await _tickets(await _ticket_app(jira))
    assert sorted(jira.calls) == ["SUP-1", "SUP-2"]  # local tickets aren't looked up
    by = {r["summary"]: r for r in rows}
    assert "Closed in Jira" not in by  # closed by an engineer: no longer listed as open
    assert by["Still open"]["status"] == "In Progress" and by["Still open"]["synced"] is True
    assert by["Local one"]["synced"] is None
    async with sm() as s:  # saved, so the chat and the Overview count agree
        t = (await s.execute(select(Ticket).where(Ticket.external_id == "SUP-1"))).scalar_one()
        assert t.status == "Done"
    rows = await _tickets(await _ticket_app(jira), open_only=False)
    assert {r["summary"]: r["status"] for r in rows}["Closed in Jira"] == "Done"


async def test_tickets_page_still_works_when_jira_is_down(sm):
    await _three_tickets(sm)
    jira = _FakeJira({"SUP-2": "In Progress"}, fail={"SUP-1"})
    by = {r["summary"]: r for r in await _tickets(await _ticket_app(jira))}
    assert by["Closed in Jira"]["synced"] is False and by["Closed in Jira"]["status"] == "Open"
    assert by["Still open"]["synced"] is True
