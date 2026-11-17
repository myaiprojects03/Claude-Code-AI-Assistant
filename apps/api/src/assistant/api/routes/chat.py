"""Public visitor chat (website widget in Phase 2) and internal staff chat."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant import audit, config_store
from assistant.api.deps import get_orchestrator
from assistant.auth import (
    ChatSession,
    StaffUser,
    get_chat_session,
    issue_session_token,
    require_roles,
)
from assistant.db import get_session
from assistant.kb import internal as internal_kb
from assistant.models import Channel, Consent, Conversation, Message, MessageRole
from assistant.orchestrator import Orchestrator
from assistant.ratelimit import limit_public

router = APIRouter(prefix="/v1", tags=["chat"])

CONSENT_TEXT_VERSION = "2026-11-v1"


class StartSession(BaseModel):
    channel: Literal["web"] = "web"
    consent: bool = Field(description="Visitor accepted the chat privacy notice")


class SessionStarted(BaseModel):
    conversation_id: uuid.UUID
    token: str
    greeting: str


class SendMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Reply(BaseModel):
    conversation_id: uuid.UUID
    reply: str
    degraded: bool = False
    sources: list[dict[str, Any]] = []  # internal assistant citations: [{n, title, ...}]


@router.post(
    "/chat/sessions",
    response_model=SessionStarted,
    status_code=201,
    dependencies=[Depends(limit_public)],
)
async def start_session(body: StartSession, session: AsyncSession = Depends(get_session)):
    if not body.consent:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "consent to the privacy notice is required"
        )
    conv = Conversation(id=uuid.uuid4(), channel=body.channel)
    session.add(conv)
    await session.flush()
    session.add(
        Consent(
            conversation_id=conv.id,
            kind="chat_processing",
            granted=True,
            text_version=CONSENT_TEXT_VERSION,
        )
    )
    greeting = await config_store.get_value(session, "chat.greeting")
    await session.commit()
    return SessionStarted(
        conversation_id=conv.id, token=issue_session_token(conv.id, conv.channel), greeting=greeting
    )


@router.post("/chat/messages", response_model=Reply, dependencies=[Depends(limit_public)])
async def send_message(
    body: SendMessage,
    chat: ChatSession = Depends(get_chat_session),
    session: AsyncSession = Depends(get_session),
    orch: Orchestrator = Depends(get_orchestrator),
):
    try:
        result = await orch.handle_turn(session, chat.conversation_id, body.text)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found") from e
    except PermissionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "conversation is closed") from e
    return Reply(
        conversation_id=result.conversation_id, reply=result.reply, degraded=result.degraded
    )


# ---------- Internal (staff) ----------

staff_any = require_roles("admin", "manager", "consultant")


@router.post("/internal/conversations", status_code=201)
async def start_internal(
    user: StaffUser = Depends(staff_any), session: AsyncSession = Depends(get_session)
) -> dict[str, uuid.UUID]:
    conv = Conversation(id=uuid.uuid4(), channel=Channel.internal, staff_sub=user.sub)
    session.add(conv)
    await session.commit()
    return {"conversation_id": conv.id}


@router.post("/internal/conversations/{conversation_id}/messages", response_model=Reply)
async def internal_message(
    conversation_id: uuid.UUID,
    body: SendMessage,
    request: Request,
    user: StaffUser = Depends(staff_any),
    session: AsyncSession = Depends(get_session),
    orch: Orchestrator = Depends(get_orchestrator),
):
    conv = await session.get(Conversation, conversation_id)
    # Same 404 whether missing or someone else's: don't leak existence.
    if conv is None or conv.channel != Channel.internal or conv.staff_sub != user.sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="internal_chat.message",
        target_type="conversation",
        target_id=str(conversation_id),
    )
    embedder = request.app.state.embedder

    async def documents(s: AsyncSession, conv_id: uuid.UUID, text: str):
        # Search with the previous question too, so follow-ups ("and the timeline?") work.
        recent = (
            (
                await s.execute(
                    select(Message.content)
                    .where(Message.conversation_id == conv_id, Message.role == MessageRole.user)
                    .order_by(Message.id.desc())
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        query = " ".join(reversed([r for r in recent if r])) or text
        note, sources = await internal_kb.retrieve_for_turn(s, embedder, user, conv_id, query)
        return note, [src.public() for src in sources]

    result = await orch.handle_turn(
        session,
        conversation_id,
        body.text,
        actor_id=user.sub,
        roles=set(user.roles),
        context=documents,
    )
    return Reply(
        conversation_id=result.conversation_id,
        reply=result.reply,
        degraded=result.degraded,
        sources=result.sources,
    )
