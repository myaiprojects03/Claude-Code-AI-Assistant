"""Effect handlers run by the outbox worker. Each returns an external reference.

Handlers receive the outbox idempotency key and pass it downstream where the target
supports de-duplication (email Message-ID, Google event id), shrinking the at-least-once window.
"""

import asyncio
import smtplib
import ssl
import uuid
from collections.abc import Awaitable, Callable
from email.message import EmailMessage
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant.booking import display
from assistant.integrations import build_calendar, build_crm, build_ticketing
from assistant.integrations.calendar import CalendarAdapter, EventSpec
from assistant.integrations.crm import CRMAdapter, LeadSync
from assistant.integrations.ticketing import TicketAdapter
from assistant.models import (
    Booking,
    BookingStatus,
    Company,
    Consultant,
    Contact,
    DoNotContact,
    Lead,
    Ticket,
)
from assistant.outbox.errors import PermanentError
from assistant.settings import get_settings

log = structlog.get_logger()

Handler = Callable[[dict[str, Any], str], Awaitable[str | None]]

__all__ = ["Handler", "PermanentError", "build_handlers"]


def _send_smtp(msg: EmailMessage) -> None:
    s = get_settings()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
        if s.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if s.smtp_username:
            smtp.login(s.smtp_username, s.smtp_password)
        smtp.send_message(msg)


async def send_email(payload: dict[str, Any], key: str) -> str:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = payload["to"]
    msg["Subject"] = payload["subject"]
    msg["Message-ID"] = f"<{key}@assistant.local>"
    msg.set_content(payload["body"])
    try:
        await asyncio.to_thread(_send_smtp, msg)
    except smtplib.SMTPRecipientsRefused as e:
        raise PermanentError(f"recipient refused: {e}") from e
    return msg["Message-ID"]


async def slack_notify(payload: dict[str, Any], key: str) -> str:
    url = get_settings().slack_notify_webhook_url
    if not url:
        log.info("slack.disabled", text=payload.get("text"))
        return "slack-disabled"
    prefix = {"high": ":red_circle: ", "normal": "", "low": ""}.get(payload.get("urgency", ""), "")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(url, json={"text": prefix + payload["text"]})
    if 400 <= r.status_code < 500 and r.status_code != 429:
        raise PermanentError(f"slack rejected: {r.status_code} {r.text[:200]}")
    r.raise_for_status()
    return f"slack:{key[:12]}"


def make_calendar_handler(
    sm: async_sessionmaker[AsyncSession], calendar: CalendarAdapter
) -> Handler:
    async def create_event(payload: dict[str, Any], key: str) -> str:
        async with sm() as s, s.begin():
            booking = await s.get(Booking, uuid.UUID(payload["booking_id"]), with_for_update=True)
            if booking is None:
                raise PermanentError("booking not found")
            if booking.status == BookingStatus.cancelled:
                raise PermanentError("booking was cancelled")
            if booking.calendar_event_id:  # already done by an earlier attempt
                return booking.calendar_event_id
            consultant = await s.get(Consultant, booking.consultant_id)
            contact = await s.get(Contact, booking.contact_id) if booking.contact_id else None

            calendar_id = consultant.calendar_id or consultant.email
            if consultant.calendar_id:
                # Re-check right before writing: the calendar may have changed since booking.
                clashes = await calendar.busy(calendar_id, booking.starts_at, booking.ends_at)
                if clashes:
                    booking.status = BookingStatus.cancelled
                    raise PermanentError(f"consultant no longer free at {booking.starts_at}")

            who = contact.name or contact.email if contact else "visitor"
            ref = await calendar.create_event(
                EventSpec(
                    event_id=booking.idempotency_key,
                    calendar_id=calendar_id,
                    start=booking.starts_at,
                    end=booking.ends_at,
                    summary=f"Discovery call: {who}",
                    description=f"Booked by the website assistant.\nContact: "
                    f"{contact.email if contact else 'n/a'}\nTopic: {booking.topic}",
                )
            )
            booking.calendar_event_id = ref
            booking.status = BookingStatus.confirmed
            return ref

    return create_event


def make_crm_handler(sm: async_sessionmaker[AsyncSession], crm: CRMAdapter) -> Handler:
    async def sync(payload: dict[str, Any], key: str) -> str:
        # Reads the lead's *current* state, so a late retry never sends stale data.
        async with sm() as s, s.begin():
            lead = await s.get(Lead, uuid.UUID(payload["lead_id"]))
            if lead is None or lead.contact_id is None:
                raise PermanentError("lead or contact missing")
            contact = await s.get(Contact, lead.contact_id)
            company = await s.get(Company, contact.company_id) if contact.company_id else None
            ref = await crm.upsert_lead(
                LeadSync(
                    email=contact.email,
                    name=contact.name,
                    phone=contact.phone,
                    company=company.name if company else None,
                    status=lead.status,
                    summary=lead.summary or "",
                ),
                key,
            )
            if ref.startswith("hubspot:contact:"):
                contact.hubspot_id = ref.rsplit(":", 1)[1]
            return ref

    return sync


def make_calendar_cancel_handler(
    sm: async_sessionmaker[AsyncSession], calendar: CalendarAdapter
) -> Handler:
    async def cancel_event(payload: dict[str, Any], key: str) -> str:
        async with sm() as s:
            booking = await s.get(Booking, uuid.UUID(payload["booking_id"]))
            if booking is None or not booking.calendar_event_id:
                return "no-event"  # never reached the calendar: nothing to remove
            consultant = await s.get(Consultant, booking.consultant_id)
            await calendar.cancel_event(
                consultant.calendar_id or consultant.email, booking.calendar_event_id
            )
            return f"cancelled:{booking.calendar_event_id}"

    return cancel_event


def make_ticket_handlers(
    sm: async_sessionmaker[AsyncSession], tickets: TicketAdapter
) -> dict[str, Handler]:
    async def create(payload: dict[str, Any], key: str) -> str:
        async with sm() as s, s.begin():
            t = await s.get(Ticket, uuid.UUID(payload["ticket_id"]), with_for_update=True)
            if t is None:
                raise PermanentError("ticket not found")
            if t.external_id:
                return t.external_id
            company = await s.get(Company, t.company_id)
            ext = await tickets.create(
                ref=t.ref,
                summary=t.summary,
                description=t.description or "",
                priority=t.priority,
                company=company.name,
            )
            if ext:
                t.external_id = ext
            return ext or f"local:{t.ref}"

    async def comment(payload: dict[str, Any], key: str) -> str:
        async with sm() as s:
            t = await s.get(Ticket, uuid.UUID(payload["ticket_id"]))
            if t is None:
                raise PermanentError("ticket not found")
            if not t.external_id:
                if t.system != "local":  # mirror not created yet: retry after ticket.create
                    raise RuntimeError("external ticket not created yet")
                return f"local:{t.ref}"
            await tickets.comment(t.external_id, f"Client update: {payload['text']}")
            return f"{t.external_id}:comment"

    return {"ticket.create": create, "ticket.comment": comment}


def make_reminder_handler(sm: async_sessionmaker[AsyncSession]) -> Handler:
    async def remind(payload: dict[str, Any], key: str) -> str:
        async with sm() as s:
            b = await s.get(Booking, uuid.UUID(payload["booking_id"]))
            if (
                b is None
                or b.status == BookingStatus.cancelled
                or b.starts_at.isoformat() != payload["starts_at"]
            ):
                return "skipped: booking cancelled or moved"
            consultant = await s.get(Consultant, b.consultant_id)
            contact = await s.get(Contact, b.contact_id) if b.contact_id else None

            if payload["audience"] == "consultant":
                mention = f"<@{consultant.slack_user_id}> " if consultant.slack_user_id else ""
                who = (contact.name or contact.email) if contact else "a visitor"
                return await slack_notify(
                    {
                        "text": f":alarm_clock: {mention}Reminder: discovery call with "
                        f"*{who}* in 1 hour ({display(b.starts_at, 'Europe/London')}). "
                        f"Topic: {b.topic or 'n/a'}",
                        "urgency": "normal",
                    },
                    key,
                )

            if contact is None or not contact.email:
                return "skipped: no email"
            blocked = (
                await s.execute(
                    select(DoNotContact.id).where(
                        DoNotContact.channel == "email", DoNotContact.address == contact.email
                    )
                )
            ).first()
            if blocked:
                return "skipped: do-not-contact"
            when = display(
                b.starts_at, payload.get("timezone") or contact.timezone or "Europe/London"
            )
            return await send_email(
                {
                    "to": contact.email,
                    "subject": f"Reminder: your discovery call {when}",
                    "body": f"Hello {(contact.name or '').split(' ')[0] or 'there'},\n\n"
                    f"A reminder that your discovery call with {consultant.name} is on "
                    f"{when}.\n\nIf you need to change it, use the chat on our website.\n\n"
                    "Acme IT Consulting",
                },
                key,
            )

    return remind


def build_handlers(
    sm: async_sessionmaker[AsyncSession],
    *,
    crm: CRMAdapter | None = None,
    calendar: CalendarAdapter | None = None,
    tickets: TicketAdapter | None = None,
) -> dict[str, Handler]:
    calendar = calendar or build_calendar()
    return {
        "email.send": send_email,
        "slack.notify": slack_notify,
        "calendar.create_event": make_calendar_handler(sm, calendar),
        "calendar.cancel_event": make_calendar_cancel_handler(sm, calendar),
        "crm.sync": make_crm_handler(sm, crm or build_crm()),
        "booking.reminder": make_reminder_handler(sm),
        **make_ticket_handlers(sm, tickets or build_ticketing()),
    }
