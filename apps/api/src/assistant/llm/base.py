"""Provider-agnostic LLM types. Messages and tools use OpenAI-style shapes internally."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMMessage:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # tool name for role="tool"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    finish_reason: str | None = None


class LLMError(Exception):
    """Base error. `retryable` errors trigger the fallback chain."""

    def __init__(self, message: str, *, provider: str, retryable: bool, status: int | None = None):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retryable = retryable
        self.status = status


class LLMProvider(Protocol):
    name: str
    model: str

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...
