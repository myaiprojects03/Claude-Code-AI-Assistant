"""GDPR (Phase 7): subject access (export), erasure, and retention.

A data subject is found by email. Their data is everything linked to that contact, plus any
conversation in which they verified with that email:

    contact, conversations + messages, leads, bookings, handovers, tickets they raised,
    consents, verification-code records, tool intents, queued and sent messages (outbox).

Erasure anonymises rather than deletes rows, so counts and audit history stay intact while
nothing identifies the person: names, emails, phone numbers, free text and transcripts are
removed; future calls are cancelled; queued emails to them are stopped; the address goes on
the do-not-contact list (a suppression list is allowed, and is what stops us re-contacting
them). Tickets stay with the client company (contract), unlinked from the person.

Retention runs from the worker: old transcripts are erased the same way, expired codes and
old outbox rows are deleted. Periods are settings (see docs/phase-7).
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.models import (
    Booking,
    BookingStatus,
    Company,
    Consent,
    Contact,
    Conversation,
    DoNotContact,
    Handover,
    HandoverStatus,
    Lead,
    Message,
    OutboxItem,
    OutboxStatus,
    Ticket,
    ToolIntent,
    VerificationCode,
)
from assistant.tools.registry import enqueue

ERASED = "[erased under a data protection request]"
EXPIRED = "[removed after the retention period]"


def email_hash(email: str) -> str:
    """For the audit trail: proves which request it was without storing the address."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


async def _subject(session: AsyncSession, email: str) -> tuple[Contact | None, list[uuid.UUID]]:
    e = email.strip().lower()
    contact = (
        await session.execute(select(Contact).where(func.lower(Contact.email) == e))
    ).scalar_one_or_none()
    conds = [
        Conversation.id.in_(
            select(VerificationCode.conversation_id).where(func.lower(VerificationCode.email) == e)
        )
    ]
    if contact:
        conds.append(Conversation.contact_id == contact.id)
    convs = (
        (
            await session.execute(
                select(Conversation.id).where(or_(*conds), Conversation.channel != "internal")
            )
        )
        .scalars()
        .all()
    )
    return contact, list(convs)


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, uuid.UUID) else v


def _row(obj: Any, fields: list[str]) -> dict[str, Any]:
    return {f: _iso(getattr(obj, f)) for f in fields}


async def summary(session: AsyncSession, email: str) -> dict[str, Any]:
    """What we hold about this address, as counts (for the dashboard, before acting)."""
    contact, convs = await _subject(session, email)
    cid = contact.id if contact else uuid.uuid4()

    async def n(q) -> int:
        return (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()

    return {
        "found": bool(contact or convs),
        "name": contact.name if contact else None,
        "company": (await session.get(Company, contact.company_id)).name
        if contact and contact.company_id
        else None,
        "conversations": len(convs),
        "messages": await n(select(Message.id).where(Message.conversation_id.in_(convs))),
        "leads": await n(select(Lead.id).where(Lead.contact_id == cid)),
        "bookings": await n(select(Booking.id).where(Booking.contact_id == cid)),
        "tickets": await n(select(Ticket.id).where(Ticket.contact_id == cid)),
        "handovers": await n(
            select(Handover.id).where(
                or_(Handover.contact_id == cid, Handover.conversation_id.in_(convs))
            )
        ),
        "consents": await n(
            select(Consent.id).where(
                or_(Consent.contact_id == cid, Consent.conversation_id.in_(convs))
            )
        ),
        "on_do_not_contact": bool(
            (
                await session.execute(
                    select(DoNotContact.id).where(
                        DoNotContact.channel == "email",
                        func.lower(DoNotContact.address) == email.strip().lower(),
                    )
                )
            ).first()
        ),
    }


async def export(session: AsyncSession, email: str) -> dict[str, Any]:
    """Everything we hold about this person, in one JSON document (subject access request)."""
    contact, convs = await _subject(session, email)
    cid = contact.id if contact else uuid.uuid4()
    rows = lambda q: session.execute(q)  # noqa: E731

    msgs = (
        await rows(select(Message).where(Message.conversation_id.in_(convs)).order_by(Message.id))
    ).scalars()
    out: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "subject_email": email.strip().lower(),
        "contact": _row(
            contact, ["id", "name", "email", "phone", "timezone", "is_client", "created_at"]
        )
        if contact
        else None,
        "company": None,
        "conversations": [
            _row(c, ["id", "channel", "status", "verified", "created_at", "updated_at"])
            for c in (await rows(select(Conversation).where(Conversation.id.in_(convs)))).scalars()
        ],
        # Visitor and assistant messages only: tool results and system notes are internal
        # processing records, summarised by the other sections below.
        "messages": [
            _row(m, ["conversation_id", "role", "content", "created_at"])
            for m in msgs
            if m.role in ("user", "assistant") and m.content
        ],
        "leads": [
            _row(
                x,
                [
                    "id",
                    "status",
                    "need",
                    "need_category",
                    "company_size",
                    "budget_range",
                    "timeline",
                    "role",
                    "summary",
                    "created_at",
                ],
            )
            for x in (await rows(select(Lead).where(Lead.contact_id == cid))).scalars()
        ],
        "bookings": [
            _row(x, ["id", "starts_at", "ends_at", "status", "topic", "created_at"])
            for x in (await rows(select(Booking).where(Booking.contact_id == cid))).scalars()
        ],
        "tickets": [
            {
                **_row(x, ["summary", "description", "status", "priority", "created_at"]),
                "ref": x.ref,
            }
            for x in (await rows(select(Ticket).where(Ticket.contact_id == cid))).scalars()
        ],
        "handovers": [
            {**_row(x, ["reason", "urgency", "status", "summary", "created_at"]), "ref": x.ref}
            for x in (
                await rows(
                    select(Handover).where(
                        or_(Handover.contact_id == cid, Handover.conversation_id.in_(convs))
                    )
                )
            ).scalars()
        ],
        "consents": [
            _row(x, ["kind", "granted", "text_version", "at"])
            for x in (
                await rows(
                    select(Consent).where(
                        or_(Consent.contact_id == cid, Consent.conversation_id.in_(convs))
                    )
                )
            ).scalars()
        ],
        "verification_codes": [
            # Never the code itself (only a hash is stored): when one was sent, and attempts.
            _row(x, ["expires_at", "attempts"])
            for x in (
                await rows(
                    select(VerificationCode).where(
                        func.lower(VerificationCode.email) == email.strip().lower()
                    )
                )
            ).scalars()
        ],
    }
    if contact and contact.company_id:
        co = await session.get(Company, contact.company_id)
        out["company"] = {"name": co.name, "domain": co.domain} if co else None
    return out


async def erase(session: AsyncSession, email: str) -> dict[str, int]:
    """Anonymise everything about this person (right to erasure). Caller commits and audits."""
    e = email.strip().lower()
    contact, convs = await _subject(session, email)
    stats: dict[str, int] = {}
    cid = contact.id if contact else None

    async def count(stmt) -> int:
        return (await session.execute(stmt)).rowcount or 0

    stats["messages"] = await count(
        update(Message)
        .where(Message.conversation_id.in_(convs))
        .values(content=ERASED, tool_calls=None)
    )
    stats["tool_intents"] = await count(
        update(ToolIntent)
        .where(ToolIntent.conversation_id.in_(convs))
        .values(args={"erased": True})
    )
    handover_where = Handover.conversation_id.in_(convs)
    if cid:
        handover_where = or_(handover_where, Handover.contact_id == cid)
    stats["handovers"] = await count(update(Handover).where(handover_where).values(summary=ERASED))
    stats["verification_codes"] = await count(
        delete(VerificationCode).where(
            or_(
                func.lower(VerificationCode.email) == e, VerificationCode.conversation_id.in_(convs)
            )
        )
    )
    # Anything still queued to or about them is stopped; sent ones lose their content.
    queued = (
        (
            await session.execute(
                select(OutboxItem).where(
                    OutboxItem.status.in_([OutboxStatus.pending, OutboxStatus.processing])
                )
            )
        )
        .scalars()
        .all()
    )
    stopped = 0
    for item in queued:
        p = item.payload or {}
        if str(p.get("to", "")).lower() == e or p.get("conversation_id") in {str(c) for c in convs}:
            item.status, item.last_error, item.payload = (
                OutboxStatus.dead,
                "stopped: erasure request",
                {"erased": True},
            )
            stopped += 1
    stats["queued_messages_stopped"] = stopped
    if cid:
        stats["leads"] = await count(
            update(Lead)
            .where(Lead.contact_id == cid)
            .values(need=None, summary=None, current_systems=[])
        )
        future = (
            (
                await session.execute(
                    select(Booking).where(
                        Booking.contact_id == cid,
                        Booking.starts_at > datetime.now(UTC),
                        Booking.status != BookingStatus.cancelled,
                    )
                )
            )
            .scalars()
            .all()
        )
        for b in future:
            b.status = BookingStatus.cancelled
            if b.calendar_event_id:
                await enqueue(
                    session,
                    "calendar.cancel_event",
                    {"booking_id": str(b.id)},
                    key=f"erase:{b.id}:calendar",
                )
        stats["future_bookings_cancelled"] = len(future)
        await session.execute(update(Booking).where(Booking.contact_id == cid).values(topic=None))
        stats["tickets_unlinked"] = await count(
            update(Ticket).where(Ticket.contact_id == cid).values(contact_id=None)
        )
        contact.name = contact.email = contact.phone = contact.hubspot_id = None
        contact.timezone = None
        stats["contact"] = 1
    if not (
        await session.execute(
            select(DoNotContact.id).where(
                DoNotContact.channel == "email", func.lower(DoNotContact.address) == e
            )
        )
    ).first():
        session.add(DoNotContact(channel="email", address=e, reason="erasure request"))
    await session.flush()
    return stats


async def apply_retention(
    session: AsyncSession, policy: dict[str, int], now: datetime | None = None
) -> dict[str, int]:
    """Delete or anonymise data past its retention period. Caller commits."""
    now = now or datetime.now(UTC)
    stats: dict[str, int] = {}
    cutoff = now - timedelta(days=policy["conversation_days"])
    # Visitor conversations untouched for the period, with nothing still open about them.
    still_needed = or_(
        Conversation.id.in_(
            select(Handover.conversation_id).where(Handover.status != HandoverStatus.resolved)
        ),
        Conversation.id.in_(select(Booking.conversation_id).where(Booking.starts_at > now)),
    )
    old = select(Conversation.id).where(
        Conversation.updated_at < cutoff, Conversation.channel != "internal", ~still_needed
    )
    stats["messages_erased"] = (
        await session.execute(
            update(Message)
            .where(Message.conversation_id.in_(old), Message.content != EXPIRED)
            .values(content=EXPIRED, tool_calls=None)
        )
    ).rowcount or 0
    stats["handover_summaries_erased"] = (
        await session.execute(
            update(Handover)
            .where(Handover.conversation_id.in_(old), Handover.summary != EXPIRED)
            .values(summary=EXPIRED)
        )
    ).rowcount or 0
    internal_cutoff = now - timedelta(days=policy["internal_chat_days"])
    old_internal = select(Conversation.id).where(
        Conversation.updated_at < internal_cutoff, Conversation.channel == "internal"
    )
    stats["internal_messages_erased"] = (
        await session.execute(
            update(Message)
            .where(Message.conversation_id.in_(old_internal), Message.content != EXPIRED)
            .values(content=EXPIRED, tool_calls=None)
        )
    ).rowcount or 0
    stats["verification_codes_deleted"] = (
        await session.execute(
            delete(VerificationCode).where(
                VerificationCode.expires_at < now - timedelta(days=policy["code_days"])
            )
        )
    ).rowcount or 0
    stats["outbox_rows_deleted"] = (
        await session.execute(
            delete(OutboxItem).where(
                OutboxItem.status.in_([OutboxStatus.done, OutboxStatus.dead]),
                OutboxItem.next_attempt_at < now - timedelta(days=policy["outbox_days"]),
            )
        )
    ).rowcount or 0
    from assistant.models import AuditLog

    stats["audit_rows_deleted"] = (
        await session.execute(
            delete(AuditLog).where(AuditLog.at < now - timedelta(days=policy["audit_days"]))
        )
    ).rowcount or 0
    return stats
