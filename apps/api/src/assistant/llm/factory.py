"""Builds providers from settings and chains them with fallback."""

import structlog

from assistant import metrics
from assistant.llm.anthropic import AnthropicProvider
from assistant.llm.base import LLMError, LLMMessage, LLMProvider, LLMResponse, ToolSpec
from assistant.llm.fake import FakeProvider
from assistant.llm.openai_compat import OpenAICompatProvider
from assistant.settings import Settings, get_settings

log = structlog.get_logger()

GROQ_URL = "https://api.groq.com/openai/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def build_provider(name: str, s: Settings) -> LLMProvider | None:
    """Return the provider, or None if it isn't configured (missing key / disabled)."""
    if name == "groq":
        if not s.groq_api_key:
            return None
        return OpenAICompatProvider(
            name="groq",
            base_url=GROQ_URL,
            model=s.groq_model,
            api_key=s.groq_api_key,
            timeout_s=s.llm_timeout_s,
        )
    if name == "groq_alt":
        if not s.groq_api_key:
            return None
        return OpenAICompatProvider(
            name="groq_alt",
            base_url=GROQ_URL,
            model=s.groq_alt_model,
            api_key=s.groq_api_key,
            timeout_s=s.llm_timeout_s,
        )
    if name == "gemini":
        if not s.gemini_api_key:
            return None
        return OpenAICompatProvider(
            name="gemini",
            base_url=GEMINI_URL,
            model=s.gemini_model,
            api_key=s.gemini_api_key,
            timeout_s=s.llm_timeout_s,
        )
    if name == "ollama":
        return OpenAICompatProvider(
            name="ollama",
            base_url=s.ollama_base_url,
            model=s.ollama_model,
            timeout_s=s.ollama_timeout_s,
            default_max_tokens=s.ollama_max_tokens,
        )
    if name == "anthropic":
        if not (s.anthropic_enabled and s.anthropic_api_key):
            return None  # paid; off unless explicitly enabled
        return AnthropicProvider(
            api_key=s.anthropic_api_key, model=s.anthropic_model, timeout_s=s.llm_timeout_s
        )
    if name == "fake":
        return FakeProvider()
    raise ValueError(f"unknown LLM provider '{name}'")


class FallbackProvider:
    """Tries providers in order. Moves on only for retryable errors (429, 5xx, timeouts)."""

    def __init__(self, providers: list[LLMProvider]):
        if not providers:
            raise ValueError("no LLM providers configured: set GROQ_API_KEY or LLM_CHAIN")
        self.providers = providers
        self.name = "+".join(p.name for p in providers)
        self.model = providers[0].model

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        last: LLMError | None = None
        for p in self.providers:
            try:
                resp = await p.chat(messages, tools, temperature=temperature, max_tokens=max_tokens)
                metrics.LLM_REQUESTS.labels(provider=p.name, outcome="ok").inc()
                metrics.LLM_LATENCY.labels(provider=p.name).observe(resp.latency_ms / 1000)
                return resp
            except LLMError as e:
                metrics.LLM_REQUESTS.labels(provider=p.name, outcome="error").inc()
                log.warning(
                    "llm.provider_failed",
                    provider=p.name,
                    status=e.status,
                    retryable=e.retryable,
                    error=str(e),
                )
                last = e
                if not e.retryable:
                    raise
        assert last is not None
        raise last


def build_chain(names: list[str] | None = None, s: Settings | None = None) -> LLMProvider:
    s = s or get_settings()
    providers = [p for n in (names or s.llm_chain_list) if (p := build_provider(n, s))]
    return FallbackProvider(providers)
