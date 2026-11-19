"""Privacy requests and the audit log (Phase 7). Admins only; every action is audited."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant import audit, privacy
from assistant.auth import StaffUser, require_roles
from assistant.db import get_session
from assistant.models import AuditLog
from assistant.settings import get_settings

router = APIRouter(prefix="/v1/dashboard", tags=["privacy"])
admins = require_roles("admin")
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _email(email: str) -> str:
    import re

    e = email.strip().lower()
    if not re.fullmatch(EMAIL, e) or len(e) > 320:
        raise HTTPException(422, "Enter the person's email address.")
    return e


def retention_policy() -> dict[str, int]:
    s = get_settings()
    return {
        "conversation_days": s.retention_conversation_days,
        "internal_chat_days": s.retention_internal_chat_days,
        "code_days": s.retention_code_days,
        "outbox_days": s.retention_outbox_days,
        "audit_days": s.retention_audit_days,
    }


@router.get("/privacy")
async def privacy_lookup(
    email: str = Query(..., max_length=320),
    user: StaffUser = Depends(admins),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    e = _email(email)
    result = await privacy.summary(session, e)
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="privacy.lookup",
        target_type="subject",
        target_id=privacy.email_hash(e),
    )
    await session.commit()
    return {**result, "retention": retention_policy()}


@router.get("/privacy/export")
async def privacy_export(
    email: str = Query(..., max_length=320),
    user: StaffUser = Depends(admins),
    session: AsyncSession = Depends(get_session),
):
    e = _email(email)
    data = await privacy.export(session, e)
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="privacy.export",
        target_type="subject",
        target_id=privacy.email_hash(e),
        messages=len(data["messages"]),
    )
    await session.commit()
    name = f"subject-access-{privacy.email_hash(e)}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}"'})


class EraseIn(BaseModel):
    email: str = Field(max_length=320)
    confirm: str = Field(max_length=320, description="The same email again, typed by the admin")


@router.post("/privacy/erase")
async def privacy_erase(
    body: EraseIn, user: StaffUser = Depends(admins), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    e = _email(body.email)
    if body.confirm.strip().lower() != e:
        raise HTTPException(
            422, "Type the email address again to confirm. Erasure can't be undone."
        )
    stats = await privacy.erase(session, e)
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="privacy.erase",
        target_type="subject",
        target_id=privacy.email_hash(e),
        **stats,
    )
    await session.commit()
    return {"erased": True, **stats}


# ---------- Audit log ----------


class AuditRow(BaseModel):
    id: int
    at: datetime
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]


@router.get("/audit", response_model=list[AuditRow])
async def audit_log(
    action: str | None = Query(None, max_length=80, description="Prefix, e.g. 'privacy.'"),
    actor: str | None = Query(None, max_length=100),
    target: str | None = Query(None, max_length=100),
    before_id: int | None = None,
    limit: int = Query(100, le=500),
    user: StaffUser = Depends(admins),
    session: AsyncSession = Depends(get_session),
):
    """Who accessed or changed what (ISO 27001 A.8.15). Read-only: nothing edits it."""
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if action:
        q = q.where(AuditLog.action.startswith(action))
    if actor:
        q = q.where(AuditLog.actor_id == actor)
    if target:
        q = q.where(AuditLog.target_id == target)
    if before_id:
        q = q.where(AuditLog.id < before_id)
    rows = (await session.execute(q)).scalars().all()
    return [AuditRow.model_validate(r, from_attributes=True) for r in rows]
