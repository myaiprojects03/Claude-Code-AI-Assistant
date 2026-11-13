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
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    async def plan(self, ctx: ToolContext, args: BaseModel) -> list[OutboxSpec]:
        """Side-effect tools implement this: return the effects to enqueue."""
        raise NotImplementedError


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def idempotency_key(conversation_id: uuid.UUID, tool: str, args: dict[str, Any]) -> str:
    raw = f"{conversation_id}|{tool}|{canonical_json(args)}"
    return hashlib.sha256(raw.encode()).hexdigest()


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

        inserted = await ctx.session.execute(
            pg_insert(ToolIntent)
            .values(
                id=uuid.uuid4(),
                idempotency_key=key,
                conversation_id=ctx.conversation_id,
                tool_name=tool.name,
                args=args_dict,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(ToolIntent.id)
        )
        intent_id = inserted.scalar_one_or_none()

        if intent_id is None:
            existing = (
                await ctx.session.execute(
                    select(ToolIntent).where(ToolIntent.idempotency_key == key)
                )
            ).scalar_one()
            log.info("tool.duplicate_suppressed", tool=tool.name, intent=str(existing.id))
            return {
                "status": existing.status,
                "reference": str(existing.id)[:8],
                "duplicate": True,
                "result": existing.result,
            }

        specs = await tool.plan(ctx, args)
        max_attempts = get_settings().outbox_max_attempts
        for i, spec in enumerate(specs):
            await ctx.session.execute(
                insert(OutboxItem).values(
                    idempotency_key=hashlib.sha256(f"{key}:{i}:{spec.kind}".encode()).hexdigest(),
                    intent_id=intent_id,
                    kind=spec.kind,
                    payload=spec.payload,
                    max_attempts=max_attempts,
                )
            )
        return {"status": "queued", "reference": str(intent_id)[:8]}
