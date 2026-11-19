"""End-to-end through HTTP with a scripted LLM (needs Postgres)."""

import httpx
import pytest
from sqlalchemy import select

from assistant.auth import StaffUser, get_current_staff
from assistant.llm.base import LLMResponse, ToolCall
from assistant.llm.fake import FakeProvider
from assistant.models import AuditLog, Consent, Message, OutboxItem
from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db


def tool_then_answer(messages, tools):
    """Script: first call asks for a tool, second call answers using its result."""
    if messages[-1].role == "tool":
        return LLMResponse("It's a weekday in London.", [], "fake", "fake-1")
    return LLMResponse(
        None, [ToolCall("c1", "get_current_time", {"timezone": "Europe/London"})], "fake", "fake-1"
    )


@pytest.fixture
async def client(sm):
    from assistant.api.main import create_app

    app = create_app(llm=FakeProvider(tool_then_answer), database_url=TEST_DATABASE_URL)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


def as_staff(client, *roles, sub="staff-1"):
    client.app.dependency_overrides[get_current_staff] = lambda: StaffUser(
        sub=sub, username="u", email=None, roles=frozenset(roles)
    )


async def test_health_and_ready(client):
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).json()["db"] == "ok"
    assert "http_requests_total" in (await client.get("/metrics")).text


async def test_visitor_chat_flow_with_tool_call(client, sm):
    r = await client.post("/v1/chat/sessions", json={"consent": True})
    assert r.status_code == 201
    body = r.json()
    assert "AI assistant" in body["greeting"]
    headers = {"Authorization": f"Bearer {body['token']}"}

    r = await client.post("/v1/chat/messages", json={"text": "What day is it?"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "It's a weekday in London."

    async with sm() as s:
        roles = (await s.execute(select(Message.role).order_by(Message.id))).scalars().all()
        assert roles == ["user", "assistant", "tool", "assistant"]
        assert (await s.execute(select(Consent))).scalar_one().granted


async def test_visitor_chat_requires_consent_and_token(client):
    assert (await client.post("/v1/chat/sessions", json={"consent": False})).status_code == 400
    r = await client.post("/v1/chat/messages", json={"text": "hi"})
    assert r.status_code == 401
    r = await client.post(
        "/v1/chat/messages", json={"text": "hi"}, headers={"Authorization": "Bearer junk"}
    )
    assert r.status_code == 401


async def test_role_based_access_on_admin_routes(client):
    as_staff(client, "consultant")
    assert (await client.get("/v1/conversations")).status_code == 403
    as_staff(client, "manager")
    assert (await client.get("/v1/conversations")).status_code == 200
    r = await client.post("/v1/config/chat.greeting/drafts", json={"value": "Hi"})
    assert r.status_code == 403  # publishing config is admin-only


async def test_viewing_a_conversation_is_audited(client, sm):
    tok = (await client.post("/v1/chat/sessions", json={"consent": True})).json()
    as_staff(client, "manager", sub="mgr-9")
    r = await client.get(f"/v1/conversations/{tok['conversation_id']}")
    assert r.status_code == 200
    async with sm() as s:
        row = (await s.execute(select(AuditLog))).scalar_one()
    assert (row.actor_id, row.action) == ("mgr-9", "conversation.view")


async def test_config_publish_changes_greeting_without_code(client):
    as_staff(client, "admin")
    r = await client.post("/v1/config/chat.greeting/drafts", json={"value": "Welcome!"})
    assert r.json()["version"] == 1 and r.json()["status"] == "draft"
    r = await client.post("/v1/config/chat.greeting/versions/1/publish")
    assert r.json()["status"] == "published"
    r = await client.post("/v1/config/chat.greeting/drafts", json={"value": "Hello again"})
    await client.post("/v1/config/chat.greeting/versions/2/publish")
    versions = (await client.get("/v1/config/chat.greeting")).json()
    assert [(v["version"], v["status"]) for v in versions] == [(2, "published"), (1, "archived")]
    greet = (await client.post("/v1/chat/sessions", json={"consent": True})).json()["greeting"]
    assert greet == "Hello again"


async def test_internal_chat_is_private_to_its_owner(client, sm):
    as_staff(client, "consultant", sub="alice")
    cid = (await client.post("/v1/internal/conversations")).json()["conversation_id"]
    as_staff(client, "consultant", sub="bob")
    r = await client.post(f"/v1/internal/conversations/{cid}/messages", json={"text": "hi"})
    assert r.status_code == 404


async def test_internal_side_effect_tool_goes_through_outbox(sm):
    from assistant.api.main import create_app

    calls = {"n": 0}

    def script(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(
                None,
                [
                    ToolCall(
                        "c1",
                        "send_email",
                        {"to": "colleague@acme.example", "subject": "Follow-up", "body": "Thanks"},
                    )
                ],
                "fake",
                "fake-1",
            )
        return LLMResponse("Queued the email.", [], "fake", "fake-1")

    app = create_app(llm=FakeProvider(script), database_url=TEST_DATABASE_URL)
    app.dependency_overrides[get_current_staff] = lambda: StaffUser(
        "alice", "a", None, frozenset({"consultant"})
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            cid = (await c.post("/v1/internal/conversations")).json()["conversation_id"]
            r = await c.post(
                f"/v1/internal/conversations/{cid}/messages",
                json={"text": "Please send it to colleague@acme.example"},
            )
    assert r.json()["reply"] == "Queued the email."
    async with sm() as s:
        item = (await s.execute(select(OutboxItem))).scalar_one()
    assert item.kind == "email.send" and item.status == "pending"
