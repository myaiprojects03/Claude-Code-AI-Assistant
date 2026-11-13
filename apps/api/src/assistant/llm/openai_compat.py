"""One adapter for every OpenAI-compatible endpoint: Groq, Gemini (OpenAI mode), Ollama."""

import json
import time
from typing import Any

import httpx

from assistant.llm.base import LLMError, LLMMessage, LLMResponse, ToolCall, ToolSpec, Usage

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def to_openai_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ]
            if d["content"] is None:
                d["content"] = ""
        if m.role == "tool":
            d["tool_call_id"] = m.tool_call_id
            if m.name:
                d["name"] = m.name
        out.append(d)
    return out


def to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for i, tc in enumerate(raw or []):
        fn = tc.get("function", {})
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
        except json.JSONDecodeError:
            # Surface malformed args to the tool layer, which reports it back to the model.
            args = {"__invalid_json__": args_raw}
        calls.append(
            ToolCall(id=tc.get("id") or f"call_{i}", name=fn.get("name", ""), arguments=args)
        )
    return calls


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(messages),
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = to_openai_tools(tools)
            body["tool_choice"] = "auto"

        started = time.perf_counter()
        try:
            resp = await self._client.post(self._url, headers=self._headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise LLMError(f"transport error: {e!r}", provider=self.name, retryable=True) from e

        if resp.status_code >= 400:
            raise LLMError(
                f"HTTP {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                retryable=resp.status_code in RETRYABLE_STATUS,
                status=resp.status_code,
            )

        data = resp.json()
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError) as e:
            raise LLMError(
                f"unexpected response: {str(data)[:300]}", provider=self.name, retryable=True
            ) from e
        usage = data.get("usage") or {}
        return LLMResponse(
            content=msg.get("content"),
            tool_calls=parse_tool_calls(msg.get("tool_calls")),
            provider=self.name,
            model=data.get("model", self.model),
            usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=choice.get("finish_reason"),
        )
