"""Conversation orchestrator: one brain shared by every channel.

Per turn: persist the user message -> build context (system prompt from config + history
window) -> LLM -> run tool calls -> loop until a text reply or the step limit.
The DB is committed before every LLM call so no transaction is held open across network I/O.
Tool intents/outbox rows are committed atomically with the tool-result messages.
"""

import json
import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant import alerts, config_store, guardrails
from assistant.llm.base import LLMError, LLMMessage, LLMProvider, ToolCall
from assistant.models import Channel, Conversation, ConversationStatus, Message, MessageRole
from assistant.settings import Settings, get_settings
from assistant.tools.registry import ToolContext, ToolRegistry

log = structlog.get_logger()

HANDED_OVER_REPLY = (
    "A consultant is now looking after this conversation and will reply to you directly."
)
STEP_LIMIT_REPLY = "Sorry, I couldn't complete that. A consultant will follow up with you."


@dataclass
class TurnResult:
    conversation_id: uuid.UUID
    reply: str
    provider: str | None = None
    tools_used: list[str] = field(default_factory=list)
    degraded: bool = False
    guarded: bool = False  # reply replaced by the guardrail fallback


def message_to_llm(m: Message) -> LLMMessage:
    calls = [
        ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments", {}))
        for c in (m.tool_calls or [])
    ]
    return LLMMessage(
        role=m.role,
        content=m.content,
        tool_calls=calls,  # type: ignore[arg-type]
        tool_call_id=m.tool_call_id,
        name=m.tool_name,
    )


def trim_history(messages: list[Message]) -> list[Message]:
    """A window must not start mid tool-exchange; drop leading non-user messages."""
    for i, m in enumerate(messages):
        if m.role == MessageRole.user:
            return messages[i:]
    return []


class Orchestrator:
    def __init__(self, llm: LLMProvider, tools: ToolRegistry, settings: Settings | None = None):
        self.llm = llm
        self.tools = tools
        self.settings = settings or get_settings()

    async def _history(self, session: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
        rows = (
            (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.role != MessageRole.system,
                    )
                    .order_by(Message.id.desc())
                    .limit(self.settings.history_window)
                )
            )
            .scalars()
            .all()
        )
        return trim_history(list(reversed(rows)))

    async def handle_turn(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        text: str,
        *,
        actor_id: str | None = None,
        roles: set[str] | None = None,
    ) -> TurnResult:
        conv = await session.get(Conversation, conversation_id)
        if conv is None:
            raise LookupError("conversation not found")
        structlog.contextvars.bind_contextvars(
            conversation_id=str(conversation_id), channel=conv.channel
        )

        if guardrails.flag_injection(text):
            log.warning("guardrail.injection_flagged")
        session.add(Message(conversation_id=conv.id, role=MessageRole.user, content=text))
        await session.commit()

        if conv.status == ConversationStatus.handed_over:
            return TurnResult(conv.id, HANDED_OVER_REPLY)
        if conv.status == ConversationStatus.closed:
            raise PermissionError("conversation is closed")

        system_prompt = await config_store.get_value(session, f"prompt.system.{conv.channel}")
        tool_specs = self.tools.specs_for(conv.channel)
        ctx = ToolContext(
            session=session,
            conversation_id=conv.id,
            channel=conv.channel,
            actor_id=actor_id,
            roles=roles or set(),
        )
        tools_used: list[str] = []

        for _step in range(self.settings.llm_max_tool_steps):
            history = await self._history(session, conv.id)
            msgs = [LLMMessage(role="system", content=system_prompt)]
            msgs += [message_to_llm(m) for m in history]
            try:
                resp = await self.llm.chat(msgs, tool_specs or None)
            except LLMError as e:
                await alerts.send_alert("critical", "LLM chain failed", error=str(e))
                fallback = await config_store.get_value(session, "chat.fallback_error")
                session.add(
                    Message(
                        conversation_id=conv.id,
                        role=MessageRole.assistant,
                        content=fallback,
                        provider="none",
                    )
                )
                await session.commit()
                return TurnResult(conv.id, fallback, degraded=True, tools_used=tools_used)

            content = resp.content
            guarded = False
            if not resp.tool_calls and conv.channel != Channel.internal:
                verdict = guardrails.check_reply(content or "")
                if not verdict.allowed:
                    log.warning(
                        "guardrail.blocked",
                        rule=verdict.rule,
                        match=verdict.match,
                        provider=resp.provider,
                    )
                    content = await config_store.get_value(session, "guardrail.fallback")
                    guarded = True

            session.add(
                Message(
                    conversation_id=conv.id,
                    role=MessageRole.assistant,
                    content=content,
                    tool_calls=[
                        {"id": c.id, "name": c.name, "arguments": c.arguments}
                        for c in resp.tool_calls
                    ]
                    or None,
                    provider=resp.provider,
                    model=resp.model,
                    tokens_in=resp.usage.input_tokens,
                    tokens_out=resp.usage.output_tokens,
                    latency_ms=resp.latency_ms,
                )
            )

            if not resp.tool_calls:
                await session.commit()
                return TurnResult(
                    conv.id,
                    content or "",
                    provider=resp.provider,
                    tools_used=tools_used,
                    guarded=guarded,
                )

            for call in resp.tool_calls:
                result = await self.tools.execute(ctx, call)
                tools_used.append(call.name)
                session.add(
                    Message(
                        conversation_id=conv.id,
                        role=MessageRole.tool,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        content=json.dumps(result, default=str),
                    )
                )
            await session.commit()

        log.warning("orchestrator.step_limit", steps=self.settings.llm_max_tool_steps)
        session.add(
            Message(
                conversation_id=conv.id,
                role=MessageRole.assistant,
                content=STEP_LIMIT_REPLY,
                provider="none",
            )
        )
        await session.commit()
        return TurnResult(conv.id, STEP_LIMIT_REPLY, degraded=True, tools_used=tools_used)
