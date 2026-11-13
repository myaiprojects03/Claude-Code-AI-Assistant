import uuid

from assistant.llm.base import ToolCall
from assistant.tools import ToolContext, ToolRegistry, default_tools
from assistant.tools.registry import idempotency_key

reg = ToolRegistry(default_tools())


def ctx(channel="web"):
    return ToolContext(session=None, conversation_id=uuid.uuid4(), channel=channel)  # type: ignore[arg-type]


def test_tools_filtered_by_channel():
    web = {t.name for t in reg.specs_for("web")}
    internal = {t.name for t in reg.specs_for("internal")}
    assert "send_email" not in web and "notify_team" not in web
    assert {"send_email", "notify_team", "get_current_time"} <= internal


def test_specs_are_json_schema():
    spec = next(t for t in reg.specs_for("internal") if t.name == "send_email")
    assert spec.parameters["type"] == "object"
    assert set(spec.parameters["required"]) == {"to", "subject", "body"}


async def test_unknown_or_disallowed_tool_returns_error():
    assert "error" in await reg.execute(ctx(), ToolCall("1", "nope", {}))
    # send_email exists but is not offered on the public web channel
    assert "error" in await reg.execute(ctx("web"), ToolCall("1", "send_email", {}))


async def test_invalid_args_reported_back_to_model():
    out = await reg.execute(ctx(), ToolCall("1", "get_current_time", {"timezone": 5}))
    assert out["error"] == "invalid arguments"


async def test_read_only_tool_runs():
    out = await reg.execute(ctx(), ToolCall("1", "get_current_time", {"timezone": "Europe/London"}))
    assert "iso" in out
    out = await reg.execute(ctx(), ToolCall("1", "get_current_time", {"timezone": "Mars/Base"}))
    assert "error" in out


def test_idempotency_key_ignores_arg_order_but_not_values():
    cid = uuid.uuid4()
    a = idempotency_key(cid, "t", {"x": 1, "y": 2})
    assert a == idempotency_key(cid, "t", {"y": 2, "x": 1})
    assert a != idempotency_key(cid, "t", {"x": 1, "y": 3})
    assert a != idempotency_key(uuid.uuid4(), "t", {"x": 1, "y": 2})
