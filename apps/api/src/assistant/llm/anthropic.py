"""Claude adapter (Messages API). Built for completeness; DISABLED by default (paid).

Enabled only when ANTHROPIC_ENABLED=true and ANTHROPIC_API_KEY is set.
"""

import time
from typing import Any

import httpx

from assistant.llm.base import LLMError, LLMMessage, LLMResponse, ToolCall, ToolSpec, Usage
from assistant.llm.openai_compat import RETRYABLE_STATUS

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


def to_anthropic(messages: list[LLMMessage]) -> tuple[str, list[dict[str, Any]]]:
    """Split out the system prompt and convert tool calls/results to content blocks."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    def append(role: str, blocks: list[dict[str, Any]]) -> None:
        # Anthropic requires alternating roles; merge consecutive same-role turns.
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        if m.role == "system":
            if m.content:
                system_parts.append(m.content)
        elif m.role == "user":
            append("user", [{"type": "text", "text": m.content or ""}])
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            append("assistant", blocks or [{"type": "text", "text": ""}])
        elif m.role == "tool":
            append(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content or "",
                    }
                ],
            )
    return "\n\n".join(system_parts), out


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.model = model
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        system, msgs = to_anthropic(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or 1024,
            "temperature": temperature,
            "messages": msgs,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        started = time.perf_counter()
        try:
            resp = await self._client.post(API_URL, headers=self._headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise LLMError(f"transport error: {e!r}", provider=self.name, retryable=True) from e
        if resp.status_code >= 400:
            raise LLMError(
                f"HTTP {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                retryable=resp.status_code in RETRYABLE_STATUS or resp.status_code == 529,
                status=resp.status_code,
            )
        data = resp.json()
        text_parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        calls = [
            ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
            for b in data.get("content", [])
            if b.get("type") == "tool_use"
        ]
        usage = data.get("usage") or {}
        return LLMResponse(
            content="".join(text_parts) or None,
            tool_calls=calls,
            provider=self.name,
            model=data.get("model", self.model),
            usage=Usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=data.get("stop_reason"),
        )
