import json

import httpx
import pytest
import respx

from assistant.llm.anthropic import to_anthropic
from assistant.llm.base import LLMError, LLMMessage, LLMResponse, ToolCall, ToolSpec
from assistant.llm.factory import FallbackProvider, build_chain
from assistant.llm.fake import FakeProvider
from assistant.llm.openai_compat import OpenAICompatProvider, to_openai_messages
from assistant.settings import Settings

URL = "https://llm.test/v1"


def provider(name="p1"):
    return OpenAICompatProvider(name=name, base_url=URL, model="m", api_key="k")


@respx.mock
async def test_parses_text_and_tool_calls():
    respx.post(f"{URL}/chat/completions").respond(
        json={
            "model": "m",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "get_current_time",
                                    "arguments": json.dumps({"timezone": "UTC"}),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }
    )
    tools = [ToolSpec("get_current_time", "time", {"type": "object", "properties": {}})]
    resp = await provider().chat([LLMMessage("user", "time?")], tools)
    assert resp.tool_calls == [ToolCall("c1", "get_current_time", {"timezone": "UTC"})]
    assert resp.usage.input_tokens == 12
    sent = json.loads(respx.calls.last.request.content)
    assert sent["tools"][0]["function"]["name"] == "get_current_time"
    assert respx.calls.last.request.headers["authorization"] == "Bearer k"


@respx.mock
async def test_malformed_tool_args_do_not_crash():
    respx.post(f"{URL}/chat/completions").respond(
        json={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "x", "arguments": "{not json"}}
                        ],
                    }
                }
            ]
        }
    )
    resp = await provider().chat([LLMMessage("user", "hi")])
    assert "__invalid_json__" in resp.tool_calls[0].arguments


@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (400, False), (401, False)])
@respx.mock
async def test_http_errors_classified(status, retryable):
    respx.post(f"{URL}/chat/completions").respond(status_code=status, text="nope")
    with pytest.raises(LLMError) as ei:
        await provider().chat([LLMMessage("user", "hi")])
    assert ei.value.retryable is retryable


@respx.mock
async def test_timeout_is_retryable():
    respx.post(f"{URL}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMError) as ei:
        await provider().chat([LLMMessage("user", "hi")])
    assert ei.value.retryable


class _Failing:
    def __init__(self, retryable: bool):
        self.name, self.model, self.retryable = "bad", "m", retryable

    async def chat(self, *a, **k):
        raise LLMError("boom", provider="bad", retryable=self.retryable)


async def test_fallback_moves_on_for_retryable_errors():
    ok = FakeProvider([LLMResponse("hello", [], "fake", "fake-1")])
    resp = await FallbackProvider([_Failing(True), ok]).chat([LLMMessage("user", "hi")])
    assert resp.content == "hello"


async def test_fallback_stops_on_non_retryable_errors():
    ok = FakeProvider()
    with pytest.raises(LLMError):
        await FallbackProvider([_Failing(False), ok]).chat([LLMMessage("user", "hi")])
    assert ok.calls == []


def test_chain_skips_unconfigured_and_keeps_claude_off():
    s = Settings(
        llm_chain="groq,anthropic,ollama",
        groq_api_key="",
        anthropic_api_key="sk",
        anthropic_enabled=False,
        _env_file=None,
    )
    chain = build_chain(s=s)
    assert [p.name for p in chain.providers] == ["ollama"]


def test_chain_errors_when_nothing_configured():
    with pytest.raises(ValueError):
        build_chain(s=Settings(llm_chain="groq", groq_api_key="", _env_file=None))


def test_openai_message_conversion_roundtrip():
    msgs = [
        LLMMessage("assistant", None, [ToolCall("c1", "t", {"a": 1})]),
        LLMMessage("tool", '{"ok":true}', tool_call_id="c1", name="t"),
    ]
    out = to_openai_messages(msgs)
    assert out[0]["content"] == ""
    assert json.loads(out[0]["tool_calls"][0]["function"]["arguments"]) == {"a": 1}
    assert out[1]["tool_call_id"] == "c1"


def test_anthropic_conversion_merges_tool_results_and_extracts_system():
    system, msgs = to_anthropic(
        [
            LLMMessage("system", "rules"),
            LLMMessage("user", "hi"),
            LLMMessage(
                "assistant", "checking", [ToolCall("c1", "t1", {}), ToolCall("c2", "t2", {})]
            ),
            LLMMessage("tool", "r1", tool_call_id="c1"),
            LLMMessage("tool", "r2", tool_call_id="c2"),
        ]
    )
    assert system == "rules"
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["c1", "c2"]
