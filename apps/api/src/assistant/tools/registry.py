"""Tool layer.

Read-only tools run inline. Side-effecting tools never call external APIs: they record a
ToolIntent (unique idempotency key) and enqueue outbox items in the caller's transaction.
The outbox worker performs the effect. Retrying the same call returns the original intent.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from assistant import metrics
from assistant.llm.base import ToolCall, ToolSpec
from assistant.models import OutboxItem, ToolIntent
from assistant.settings import get_settings

log = structlog.get_logger()


@dataclass
class ToolContext:
    session: AsyncSession
    conversation_id: uuid.UUID
    channel: str
    actor_id: str | None = None
    roles: set[str] = field(default_factory=set)


@dataclass
class OutboxSpec:
    kind: str
    payload: dict[str, Any]


@dataclass
class Planned:
    """What a side-effect tool decided: effects to enqueue, plus details for the model."""

    effects: list[OutboxSpec]
    result: dict[str, Any] = field(default_factory=dict)


class ToolRejected(Exception):
    """A tool refuses the call (e.g. slot just taken). Nothing is recorded; the message
    is returned to the model so it can recover (offer another slot, ask for an email...)."""


class Tool:
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]
    channels: ClassVar[set[str]]  # where the tool is offered
    side_effect: ClassVar[bool] = False

    def spec(self) -> ToolSpec:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(name=self.name, description=self.description, parameters=schema)

    async def run(self, ctx: ToolContext, args: BaseModel) -> dict[str, Any]:
        """Read-only tools implement this."""
        raise NotImplementedError

    async def plan(self, ctx: ToolContext, args: BaseModel, key: str) -> Planned:
        """Side-effect tools implement this. `key` is the intent's idempotency key; reuse it
        for anything that must also be unique downstream (booking rows, calendar event ids).
        May write DB rows on ctx.session (same transaction) and may raise ToolRejected."""
        raise NotImplementedError


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def idempotency_key(conversation_id: uuid.UUID, tool: str, args: dict[str, Any]) -> str:
    raw = f"{conversation_id}|{tool}|{canonical_json(args)}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    *,
    key: str,
    intent_id: uuid.UUID | None = None,
) -> bool:
    """Add an outbox item unless one with this key already exists. Returns True if added."""
    res = await session.execute(
        pg_insert(OutboxItem)
        .values(
            idempotency_key=hashlib.sha256(f"{key}:{kind}".encode()).hexdigest(),
            intent_id=intent_id,
            kind=kind,
            payload=payload,
            max_attempts=get_settings().outbox_max_attempts,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(OutboxItem.id)
    )
    return res.scalar_one_or_none() is not None


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {t.name: t for t in tools}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs_for(self, channel: str) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values() if channel in t.channels]

    async def execute(self, ctx: ToolContext, call: ToolCall) -> dict[str, Any]:
        """Never raises for model mistakes; returns an error payload the model can read."""
        tool = self._tools.get(call.name)
        if tool is None or ctx.channel not in tool.channels:
            metrics.TOOL_CALLS.labels(tool=call.name, outcome="unknown").inc()
            return {"error": f"tool '{call.name}' is not available here"}
        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as e:
            metrics.TOOL_CALLS.labels(tool=call.name, outcome="invalid_args").inc()
            return {"error": "invalid arguments", "details": e.errors(include_url=False)}

        try:
            if tool.side_effect:
                result = await self._execute_side_effect(ctx, tool, args)
            else:
                result = await tool.run(ctx, args)
        except ToolRejected as e:
            metrics.TOOL_CALLS.labels(tool=call.name, outcome="rejected").inc()
            return {"error": str(e)}
        except Exception as e:  # tool bug: log, don't crash the conversation
            log.exception("tool.failed", tool=call.name)
            metrics.TOOL_CALLS.labels(tool=call.name, outcome="error").inc()
            return {"error": f"tool failed: {type(e).__name__}"}
        metrics.TOOL_CALLS.labels(tool=call.name, outcome="ok").inc()
        return result

    async def _execute_side_effect(
        self, ctx: ToolContext, tool: Tool, args: BaseModel
    ) -> dict[str, Any]:
        args_dict = args.model_dump(mode="json")
        key = idempotency_key(ctx.conversation_id, tool.name, args_dict)

        existing = (
            await ctx.session.execute(select(ToolIntent).where(ToolIntent.idempotency_key == key))
        ).scalar_one_or_none()
        if existing is not None:
            log.info("tool.duplicate_suppressed", tool=tool.name, intent=str(existing.id))
            return {
                "status": existing.status,
                "reference": str(existing.id)[:8],
                "duplicate": True,
                "result": existing.result,
            }

        # Savepoint: if the tool rejects the call (or a concurrent identical call wins the
        # race on the unique key), nothing from this attempt is left behind.
        try:
            async with ctx.session.begin_nested():
                intent_id = uuid.uuid4()
                ctx.session.add(
                    ToolIntent(
                        id=intent_id,
                        idempotency_key=key,
                        conversation_id=ctx.conversation_id,
                        tool_name=tool.name,
                        args=args_dict,
                        status="pending",
                    )
                )
                await ctx.session.flush()
                planned = await tool.plan(ctx, args, key)
                for i, spec in enumerate(planned.effects):
                    await enqueue(
                        ctx.session, spec.kind, spec.payload, key=f"{key}:{i}", intent_id=intent_id
                    )
        except IntegrityError as e:
            if "uq_tool_intents_idempotency_key" not in str(e.orig):
                raise
            log.info("tool.duplicate_race", tool=tool.name)
            return {"status": "pending", "duplicate": True}
        return {"status": "queued", "reference": str(intent_id)[:8], **planned.result}
