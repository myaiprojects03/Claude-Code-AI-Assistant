"""Scripted provider for tests and offline demos. No network, no cost."""

from collections.abc import Callable

from assistant.llm.base import LLMMessage, LLMResponse, ToolSpec

Script = Callable[[list[LLMMessage], list[ToolSpec] | None], LLMResponse]


class FakeProvider:
    def __init__(self, responses: list[LLMResponse] | Script | None = None, name: str = "fake"):
        self.name = name
        self.model = "fake-1"
        self._responses = responses
        self.calls: list[tuple[list[LLMMessage], list[ToolSpec] | None]] = []

    async def chat(self, messages, tools=None, *, temperature=0.2, max_tokens=None) -> LLMResponse:
        self.calls.append((list(messages), tools))
        if callable(self._responses):
            return self._responses(messages, tools)
        if self._responses:
            return self._responses.pop(0)
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return LLMResponse(
            content=f"(fake) You said: {last_user}",
            tool_calls=[],
            provider=self.name,
            model=self.model,
        )
