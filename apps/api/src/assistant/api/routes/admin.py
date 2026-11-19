"""Staff read APIs and no-code config. The Phase 6 dashboard is built on these."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from assistant import audit, config_store
from assistant import handover as handover_engine
from assistant import rules as rules_schema
from assistant.auth import StaffUser, require_roles
from assistant.db import get_session
from assistant.kb import public as public_kb
from assistant.models import (
    Channel,
    ConfigVersion,
    Consultant,
    Conversation,
    Handover,
    HandoverStatus,
)
from assistant.settings import get_settings

router = APIRouter(prefix="/v1", tags=["admin"])
managers = require_roles("admin", "manager")
admins = require_roles("admin")


class ConversationOut(BaseModel):
    id: uuid.UUID
    channel: str
    status: str
    verified: bool
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: int
    role: str
    content: str | None
    tool_name: str | None
    tool_calls: list[dict[str, Any]] | None
    provider: str | None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    channel: str | None = None,
    status_: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    before: datetime | None = None,
    user: StaffUser = Depends(managers),
    session: AsyncSession = Depends(get_session),
):
    # Internal-assistant chats are private to the staff member who started them.
    q = (
        select(Conversation)
        .where(Conversation.channel != Channel.internal)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
    )
    if channel:
        q = q.where(Conversation.channel == channel)
    if status_:
        q = q.where(Conversation.status == status_)
    if before:
        q = q.where(Conversation.created_at < before)
    rows = (await session.execute(q)).scalars().all()
    return [ConversationOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    user: StaffUser = Depends(managers),
    session: AsyncSession = Depends(get_session),
):
    conv = (
        await session.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == conversation_id, Conversation.channel != Channel.internal)
        )
    ).scalar_one_or_none()
    if conv is None:  # internal chats: same 404, they're private to their author
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="conversation.view",
        target_type="conversation",
        target_id=str(conversation_id),
    )
    await session.commit()
    return ConversationDetail.model_validate(conv, from_attributes=True)


# ---------- Config ----------


class ConfigDraft(BaseModel):
    value: Any


def _check_writable(key: str) -> None:
    """Only the settings the dashboard offers can be written. The assistants' system prompts
    hold the safety rules and change through code review (Phase 7 finding: the API used to
    accept any key, so an admin token could rewrite them)."""
    from assistant.api.routes.dashboard import EDITABLE_CONFIG

    if key not in EDITABLE_CONFIG and not key.startswith(public_kb.CONTENT_PREFIX):
        raise HTTPException(403, f"'{key}' can't be changed from the dashboard")


def _check_rules(key: str, value: Any) -> Any:
    """Rule settings must match their schema, or the assistant could break on publish."""
    try:
        return rules_schema.validate(key, value)
    except ValueError as e:
        raise HTTPException(422, f"These settings can't be saved: {e}") from e


def _check_content(key: str, value: Any) -> None:
    """Public pages (content.public.<slug>) must be markdown that passes the reply rules."""
    if not key.startswith(public_kb.CONTENT_PREFIX):
        return
    if not public_kb.SLUG.fullmatch(public_kb.slug_for(key)):
        raise HTTPException(422, "Page names use lowercase letters, digits and hyphens.")
    if not isinstance(value, str):
        raise HTTPException(422, "A page must be markdown text.")
    if issues := public_kb.content_issues(value):
        raise HTTPException(
            422, "Public content can't include prices, rates or promises. " + "; ".join(issues)
        )


class ConfigOut(BaseModel):
    key: str
    version: int
    status: str
    value: Any
    created_by: str | None
    created_at: datetime
    published_at: datetime | None


@router.get("/config/{key}", response_model=list[ConfigOut])
async def list_config_versions(
    key: str, user: StaffUser = Depends(managers), session: AsyncSession = Depends(get_session)
):
    rows = (
        (
            await session.execute(
                select(ConfigVersion)
                .where(ConfigVersion.key == key)
                .order_by(ConfigVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ConfigOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/config/{key}/drafts", response_model=ConfigOut, status_code=201)
async def create_config_draft(
    key: str,
    body: ConfigDraft,
    user: StaffUser = Depends(admins),
    session: AsyncSession = Depends(get_session),
):
    _check_writable(key)
    _check_content(key, body.value)
    value = _check_rules(key, body.value)
    row = await config_store.create_draft(session, key, value, user.username or user.sub)
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="config.draft",
        target_type="config",
        target_id=f"{key}@{row.version}",
    )
    await session.commit()
    await session.refresh(row)
    return ConfigOut.model_validate(row, from_attributes=True)


@router.post("/config/{key}/versions/{version}/publish", response_model=ConfigOut)
async def publish_config(
    key: str,
    version: int,
    request: Request,
    user: StaffUser = Depends(admins),
    session: AsyncSession = Depends(get_session),
):
    _check_writable(key)
    if key.startswith(public_kb.CONTENT_PREFIX):
        draft = (
            await session.execute(
                select(ConfigVersion.value).where(
                    ConfigVersion.key == key, ConfigVersion.version == version
                )
            )
        ).scalar_one_or_none()
        if draft is not None:
            _check_content(key, draft)
    if key in rules_schema.SCHEMAS:
        target = (
            await session.execute(
                select(ConfigVersion.value).where(
                    ConfigVersion.key == key, ConfigVersion.version == version
                )
            )
        ).scalar_one_or_none()
        if target is not None:
            _check_rules(key, target)
    try:
        row = await config_store.publish(session, key, version)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except IntegrityError as e:  # concurrent publish of the same key
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "concurrent publish, retry") from e
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="config.publish",
        target_type="config",
        target_id=f"{key}@{version}",
    )
    if key.startswith(public_kb.CONTENT_PREFIX):
        # Same transaction: the new text is searchable the moment the publish commits.
        stats = await public_kb.ingest_dir(
            session,
            request.app.state.embedder,
            Path(get_settings().kb_content_dir),
            commit=False,
        )
        audit.record(
            session,
            actor_type="staff",
            actor_id=user.sub,
            action="content.reindex",
            target_type="config",
            target_id=f"{key}@{version}",
            **stats,
        )
    await session.commit()
    await session.refresh(row)
    return ConfigOut.model_validate(row, from_attributes=True)


# ---------- Handovers ----------

staff_any = require_roles("admin", "manager", "consultant")


class HandoverOut(BaseModel):
    id: uuid.UUID
    ref: str
    conversation_id: uuid.UUID
    reason: str
    urgency: str
    route_to: str
    assigned_to: str | None  # None = general team queue
    summary: str
    status: str
    created_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None  # staff sub
    acknowledged_by_name: str | None
    resolved_at: datetime | None
    resolved_by_name: str | None
    # What the signed-in person may do (the API enforces the same rules).
    can_take: bool = False
    can_resolve: bool = False
    can_reassign: bool = False


def _out(h: Handover, user: StaffUser) -> HandoverOut:
    out = HandoverOut.model_validate(h, from_attributes=True)
    manager = user.has_any("admin", "manager")
    open_ = h.status == HandoverStatus.open
    out.can_take = open_ and handover_engine.may_take(h, user)
    out.can_resolve = h.status != HandoverStatus.resolved and (
        manager or h.acknowledged_by == user.sub or (open_ and out.can_take)
    )
    out.can_reassign = manager and h.status != HandoverStatus.resolved
    return out


@router.get("/handovers", response_model=list[HandoverOut])
async def list_handovers(
    status_: str | None = Query("open", alias="status"),
    limit: int = Query(50, le=200),
    user: StaffUser = Depends(staff_any),
    session: AsyncSession = Depends(get_session),
):
    q = select(Handover).order_by(Handover.created_at.desc()).limit(limit)
    if status_:
        q = q.where(Handover.status == status_)
    rows = (await session.execute(q)).scalars().all()
    return [_out(r, user) for r in rows]


class Assignee(BaseModel):
    name: str
    email: str


@router.get("/handovers/assignees", response_model=list[Assignee])
async def handover_assignees(
    user: StaffUser = Depends(managers), session: AsyncSession = Depends(get_session)
):
    """People a manager can pass a handover to (the consultants list, by sign-in email)."""
    rows = (await session.execute(select(Consultant).order_by(Consultant.name))).scalars()
    return [Assignee(name=c.name, email=c.email.lower()) for c in rows]


async def _act(session, handover_id, user, action, fn, **details):
    try:
        h = await fn()
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "handover not found") from e
    except handover_engine.HandoverRefused as e:
        await session.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action=action,
        target_type="handover",
        target_id=h.ref,
        **details,
    )
    await session.commit()
    return _out(h, user)


@router.post("/handovers/{handover_id}/acknowledge", response_model=HandoverOut)
async def acknowledge_handover(
    handover_id: uuid.UUID,
    user: StaffUser = Depends(staff_any),
    session: AsyncSession = Depends(get_session),
):
    return await _act(
        session,
        handover_id,
        user,
        "handover.acknowledge",
        lambda: handover_engine.acknowledge(session, handover_id, user),
    )


@router.post("/handovers/{handover_id}/resolve", response_model=HandoverOut)
async def resolve_handover(
    handover_id: uuid.UUID,
    user: StaffUser = Depends(staff_any),
    session: AsyncSession = Depends(get_session),
):
    return await _act(
        session,
        handover_id,
        user,
        "handover.resolve",
        lambda: handover_engine.resolve(session, handover_id, user),
    )


class ReassignIn(BaseModel):
    # None or "" = back to the general team queue. Plain format check: staff sign-ins may
    # use reserved domains such as example.test.
    to: str | None = Field(None, max_length=320, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/handovers/{handover_id}/reassign", response_model=HandoverOut)
async def reassign_handover(
    handover_id: uuid.UUID,
    body: ReassignIn,
    user: StaffUser = Depends(managers),
    session: AsyncSession = Depends(get_session),
):
    return await _act(
        session,
        handover_id,
        user,
        "handover.reassign",
        lambda: handover_engine.reassign(session, handover_id, user, body.to or None),
        to=body.to or "team queue",
    )
