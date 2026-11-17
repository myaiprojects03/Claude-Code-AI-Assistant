"""Staff read APIs and no-code config. The Phase 6 dashboard is built on these."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from assistant import audit, config_store
from assistant import handover as handover_engine
from assistant.auth import StaffUser, require_roles
from assistant.db import get_session
from assistant.models import ConfigVersion, Conversation, Handover

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
    q = select(Conversation).order_by(Conversation.created_at.desc()).limit(limit)
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
            .where(Conversation.id == conversation_id)
        )
    ).scalar_one_or_none()
    if conv is None:
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
    row = await config_store.create_draft(session, key, body.value, user.sub)
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
    user: StaffUser = Depends(admins),
    session: AsyncSession = Depends(get_session),
):
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
    summary: str
    status: str
    created_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None


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
    return [HandoverOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/handovers/{handover_id}/acknowledge", response_model=HandoverOut)
async def acknowledge_handover(
    handover_id: uuid.UUID,
    user: StaffUser = Depends(staff_any),
    session: AsyncSession = Depends(get_session),
):
    try:
        h = await handover_engine.acknowledge(session, handover_id, user.sub)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "handover not found") from e
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="handover.acknowledge",
        target_type="handover",
        target_id=h.ref,
    )
    await session.commit()
    return HandoverOut.model_validate(h, from_attributes=True)
