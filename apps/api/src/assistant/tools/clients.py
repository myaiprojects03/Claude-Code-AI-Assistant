"""Tools for existing clients: verification, tickets, projects, bookings, handover.

Isolation rule: every lookup is filtered by the *verified* contact's company (tickets,
projects) or contact (bookings). A reference from another client is answered exactly like a
reference that doesn't exist.
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, EmailStr, Field, field_validator
from sqlalchemy import String, cast, select

from assistant import booking as bk
from assistant import config_store, handover, identity, reminders
from assistant.integrations.calendar import CalendarAdapter
from assistant.integrations.ticketing import TicketAdapter
from assistant.models import (
    Booking,
    BookingStatus,
    Consultant,
    Project,
    Ticket,
    TicketPriority,
)
from assistant.tools.registry import OutboxSpec, Planned, Tool, ToolContext, ToolRejected

PUBLIC = {"web", "whatsapp"}


def _tz(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown timezone '{name}'") from e
    return name


class _TZ(BaseModel):
    timezone: str = Field("Europe/London", description="Visitor's IANA timezone")

    @field_validator("timezone")
    @classmethod
    def _v(cls, v: str) -> str:
        return _tz(v)


# ---------- Verification ----------


class SendCodeArgs(BaseModel):
    email: EmailStr = Field(description="The email address they use with us")


class SendVerificationCode(Tool):
    name = "send_verification_code"
    description = (
        "Start identity verification for an existing client by emailing a one-time "
        "code. Required before discussing their tickets, projects or bookings."
    )
    args_model = SendCodeArgs
    channels = PUBLIC

    async def run(self, ctx: ToolContext, args: SendCodeArgs) -> dict[str, Any]:
        # Use the address exactly as the visitor typed it. Models sometimes mis-copy emails
        # (eval finding: "priya.shah" -> "priyaa.shah"), and an unknown address silently gets
        # no code by design, so a transcription slip would otherwise strand the visitor.
        typed = await identity.emails_typed_by_visitor(ctx.session, ctx.conversation_id)
        email = args.email.lower()
        corrected = False
        if email not in typed:
            if len(typed) != 1:
                raise ToolRejected("Ask the visitor to type the email address they use with us.")
            email, corrected = typed[0], True
        note = await identity.send_code(ctx.session, ctx.conversation_id, email)
        out: dict[str, Any] = {"note": note, "email_used": email}
        if corrected:
            out["correction"] = "Used the address exactly as the visitor typed it."
        return out


class VerifyCodeArgs(BaseModel):
    code: str = Field(pattern=r"^\s*\d{6}\s*$", description="The 6-digit code they received")


class VerifyCode(Tool):
    name = "verify_code"
    description = "Check the 6-digit verification code the visitor typed."
    args_model = VerifyCodeArgs
    channels = PUBLIC

    async def run(self, ctx: ToolContext, args: VerifyCodeArgs) -> dict[str, Any]:
        # The code must come from the visitor, not the model: it has to appear in their
        # latest message. A guessed or invented code is refused before it's even checked
        # (and doesn't use up one of the visitor's attempts).
        code = args.code.strip()
        last_user = await identity.latest_visitor_message(ctx.session, ctx.conversation_id)
        if code not in re.findall(r"\d{6}", (last_user or "").replace(" ", "")):
            raise ToolRejected(
                "Only use a code the visitor has typed in their latest message. "
                "Ask them to type the 6-digit code from their email."
            )
        contact = await identity.check_code(ctx.session, ctx.conversation_id, code)
        ctx.verified = True  # unlocks the client tools for the rest of this turn
        return {"verified": True, "name": contact.name, "is_client": contact.is_client}


# ---------- Tickets ----------


async def _ticket_for(ctx: ToolContext, ref: str) -> tuple[Ticket, identity.VerifiedClient]:
    vc = await identity.require_verified(ctx.session, ctx.conversation_id, client=True)
    digits = "".join(ch for ch in ref if ch.isdigit())
    ticket = None
    if digits:
        ticket = (
            await ctx.session.execute(
                select(Ticket).where(
                    Ticket.number == int(digits), Ticket.company_id == vc.company.id
                )
            )
        ).scalar_one_or_none()
    if ticket is None:  # same answer for "doesn't exist" and "belongs to someone else"
        raise ToolRejected(f"No ticket {ref} found for {vc.company.name}. Offer list_my_tickets.")
    return ticket, vc


async def _live_status(adapter: TicketAdapter, t: Ticket) -> str:
    if t.external_id:
        try:
            if status := await adapter.status(t.external_id):
                t.status = status  # cache the latest status locally
        except Exception:  # read-only refresh: fall back to the last known status
            pass
    return t.status


class NoArgs(BaseModel):
    pass


class ListMyTickets(Tool):
    name = "list_my_tickets"
    description = "List the verified client's company's recent support tickets and statuses."
    args_model = NoArgs
    channels = PUBLIC
    requires_verification = True

    def __init__(self, adapter: TicketAdapter):
        self.adapter = adapter

    async def run(self, ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
        vc = await identity.require_verified(ctx.session, ctx.conversation_id, client=True)
        rows = (
            (
                await ctx.session.execute(
                    select(Ticket)
                    .where(Ticket.company_id == vc.company.id)
                    .order_by(Ticket.updated_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        return {
            "company": vc.company.name,
            "tickets": [
                {
                    "ref": t.ref,
                    "summary": t.summary,
                    "status": await _live_status(self.adapter, t),
                    "priority": t.priority,
                    "updated": t.updated_at.date().isoformat(),
                }
                for t in rows
            ],
        }


class TicketRefArgs(BaseModel):
    ref: str = Field(max_length=20, description="Ticket reference, e.g. T-1001")


class GetTicket(Tool):
    name = "get_ticket"
    description = "Get the status and latest updates of one of the client's tickets."
    args_model = TicketRefArgs
    channels = PUBLIC
    requires_verification = True

    def __init__(self, adapter: TicketAdapter):
        self.adapter = adapter

    async def run(self, ctx: ToolContext, args: TicketRefArgs) -> dict[str, Any]:
        t, _ = await _ticket_for(ctx, args.ref)
        return {
            "ref": t.ref,
            "summary": t.summary,
            "status": await _live_status(self.adapter, t),
            "priority": t.priority,
            "opened": t.created_at.date().isoformat(),
            "latest_updates": (t.updates or [])[-3:],
        }


class CreateTicketArgs(BaseModel):
    summary: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=5, max_length=4000)
    priority: Literal["low", "normal", "high", "urgent"] = Field(
        "normal", description="urgent = business stopped; high = major impact"
    )


class CreateTicket(Tool):
    name = "create_ticket"
    description = (
        "Raise a support ticket for the verified client. Ask for a clear summary, "
        "what happened and the impact first."
    )
    args_model = CreateTicketArgs
    channels = PUBLIC
    requires_verification = True
    side_effect = True

    def __init__(self, adapter: TicketAdapter):
        self.adapter = adapter

    async def plan(self, ctx: ToolContext, args: CreateTicketArgs, key: str) -> Planned:
        vc = await identity.require_verified(ctx.session, ctx.conversation_id, client=True)
        t = Ticket(
            id=uuid.uuid4(),
            company_id=vc.company.id,
            contact_id=vc.contact.id,
            conversation_id=ctx.conversation_id,
            summary=args.summary,
            description=args.description,
            priority=args.priority,
            system=self.adapter.name,
            updates=[
                {
                    "at": datetime.now(UTC).isoformat(),
                    "by": "client",
                    "text": "Ticket raised via the website assistant.",
                }
            ],
        )
        ctx.session.add(t)
        await ctx.session.flush()  # assigns t.number

        result: dict[str, Any] = {"ticket": t.ref, "ticket_status": t.status}
        if args.priority in (TicketPriority.urgent, TicketPriority.high):
            # Urgent technical issues go straight to a person, not just the queue.
            h, _ = await handover.create_handover(
                ctx.session,
                ctx.conversation_id,
                "technical_issue",
                f"{t.ref} ({args.priority}): {args.summary}",
                step_aside=False,
            )
            result["escalated"] = h.ref
        return Planned(
            effects=[
                OutboxSpec("ticket.create", {"ticket_id": str(t.id)}),
                OutboxSpec(
                    "slack.notify",
                    {
                        "text": f":ticket: New ticket *{t.ref}* ({args.priority}) from "
                        f"{vc.company.name}: {args.summary}",
                        "urgency": "high" if args.priority in ("high", "urgent") else "normal",
                    },
                ),
                OutboxSpec(
                    "email.send",
                    {
                        "to": vc.contact.email,
                        "subject": f"We've logged your ticket {t.ref}",
                        "body": f"Hello {(vc.contact.name or '').split(' ')[0] or 'there'},\n\n"
                        f"Your ticket {t.ref} has been logged: {args.summary}\n"
                        f"Priority: {args.priority}\n\nOur team will be in touch. You can "
                        "check its status any time in the website chat.\n\nAcme IT Consulting",
                    },
                ),
            ],
            result=result,
        )


class TicketUpdateArgs(BaseModel):
    ref: str = Field(max_length=20)
    update: str = Field(min_length=2, max_length=2000, description="What the client added")


class AddTicketUpdate(Tool):
    name = "add_ticket_update"
    description = "Add the client's new information to one of their existing tickets."
    args_model = TicketUpdateArgs
    channels = PUBLIC
    requires_verification = True
    side_effect = True

    async def plan(self, ctx: ToolContext, args: TicketUpdateArgs, key: str) -> Planned:
        t, vc = await _ticket_for(ctx, args.ref)
        t.updates = [
            *(t.updates or []),
            {"at": datetime.now(UTC).isoformat(), "by": "client", "text": args.update},
        ]
        return Planned(
            effects=[
                OutboxSpec("ticket.comment", {"ticket_id": str(t.id), "text": args.update}),
                OutboxSpec(
                    "slack.notify",
                    {
                        "text": f":speech_balloon: Update on *{t.ref}* from {vc.company.name}: "
                        f"{args.update[:500]}",
                        "urgency": "normal",
                    },
                ),
            ],
            result={"ticket": t.ref},
        )


# ---------- Projects ----------


class GetProjectUpdates(Tool):
    name = "get_project_updates"
    description = "Latest status of the verified client's projects with us."
    args_model = NoArgs
    channels = PUBLIC
    requires_verification = True

    async def run(self, ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
        vc = await identity.require_verified(ctx.session, ctx.conversation_id, client=True)
        rows = (
            (
                await ctx.session.execute(
                    select(Project)
                    .where(Project.company_id == vc.company.id)
                    .order_by(Project.name)
                )
            )
            .scalars()
            .all()
        )
        return {
            "company": vc.company.name,
            "projects": [
                {
                    "name": p.name,
                    "status": p.status,
                    "latest_update": p.summary,
                    "next_milestone": p.next_milestone,
                    "next_milestone_date": p.next_milestone_date.isoformat()
                    if p.next_milestone_date
                    else None,
                }
                for p in rows
            ],
            "note": "Share these as written; don't add dates or promises of your own.",
        }


# ---------- Bookings ----------


async def _booking_for(ctx: ToolContext, booking_id: str) -> tuple[Booking, Any]:
    vc = await identity.require_verified(ctx.session, ctx.conversation_id)
    prefix = booking_id.strip().lower()[:36]
    b = None
    if len(prefix) >= 6:
        b = (
            (
                await ctx.session.execute(
                    select(Booking).where(
                        cast(Booking.id, String).like(f"{prefix}%"),
                        Booking.contact_id == vc.contact.id,
                        Booking.status != BookingStatus.cancelled,
                        Booking.starts_at > datetime.now(UTC),
                    )
                )
            )
            .scalars()
            .first()
        )
    if b is None:
        raise ToolRejected("No upcoming booking with that reference. Call list_my_bookings.")
    return b, vc


class ListMyBookings(Tool):
    name = "list_my_bookings"
    description = "List the verified person's upcoming discovery calls."
    args_model = _TZ
    channels = PUBLIC
    requires_verification = True

    async def run(self, ctx: ToolContext, args: _TZ) -> dict[str, Any]:
        vc = await identity.require_verified(ctx.session, ctx.conversation_id)
        rows = (
            await ctx.session.execute(
                select(Booking, Consultant.name)
                .join(Consultant, Consultant.id == Booking.consultant_id)
                .where(
                    Booking.contact_id == vc.contact.id,
                    Booking.status != BookingStatus.cancelled,
                    Booking.starts_at > datetime.now(UTC),
                )
                .order_by(Booking.starts_at)
            )
        ).all()
        return {
            "bookings": [
                {
                    "booking_id": str(b.id)[:8],
                    "when": bk.display(b.starts_at, args.timezone),
                    "with": name.split()[0],
                    "topic": b.topic,
                }
                for b, name in rows
            ]
        }


class RescheduleArgs(_TZ):
    booking_id: str = Field(max_length=36, description="booking_id from list_my_bookings")
    new_start: AwareDatetime = Field(description="Exact 'start' from find_available_slots")


class RescheduleBooking(Tool):
    name = "reschedule_booking"
    description = (
        "Move a verified person's upcoming call to a new slot they chose from find_available_slots."
    )
    args_model = RescheduleArgs
    channels = PUBLIC
    requires_verification = True
    side_effect = True

    def __init__(self, calendar: CalendarAdapter):
        self.calendar = calendar

    async def plan(self, ctx: ToolContext, args: RescheduleArgs, key: str) -> Planned:
        s = ctx.session
        old, vc = await _booking_for(ctx, args.booking_id)
        old.status = BookingStatus.cancelled  # frees the old slot (rolled back if we reject)
        await s.flush()
        rules = await config_store.get_value(s, "booking.rules")
        try:
            new = await bk.reserve(
                s,
                self.calendar,
                rules,
                args.new_start,
                key=key,
                contact_id=vc.contact.id,
                conversation_id=ctx.conversation_id,
                topic=old.topic,
            )
        except bk.SlotUnavailable as e:
            raise ToolRejected(f"Sorry, {e}. Offer other slots from find_available_slots.") from e
        consultant = await s.get(Consultant, new.consultant_id)
        vc.contact.timezone = args.timezone
        before, after = (
            bk.display(old.starts_at, args.timezone),
            bk.display(new.starts_at, args.timezone),
        )
        hours = await config_store.get_value(s, "messaging.hours")
        who = vc.contact.name or vc.contact.email
        return Planned(
            effects=[
                OutboxSpec("calendar.cancel_event", {"booking_id": str(old.id)}),
                OutboxSpec("calendar.create_event", {"booking_id": str(new.id)}),
                OutboxSpec(
                    "email.send",
                    {
                        "to": vc.contact.email,
                        "subject": f"Your call has moved to {after}",
                        "body": f"Hello,\n\nYour discovery call has been moved.\n\nWas: {before}\n"
                        f"Now: {after}\nWith: {consultant.name}\n\nAcme IT Consulting",
                    },
                ),
                OutboxSpec(
                    "slack.notify",
                    {
                        "text": f":arrows_counterclockwise: Call with {who} moved from "
                        f"{before} to {after} ({consultant.name}).",
                        "urgency": "normal",
                    },
                ),
                *[
                    e
                    for e in reminders.booking_messages(
                        new, vc.contact, consultant, args.timezone, hours, datetime.now(UTC)
                    )
                    if e.kind == "booking.reminder"
                ],
            ],
            result={"was": before, "now": after, "booking_id": str(new.id)[:8]},
        )


class CancelArgs(BaseModel):
    booking_id: str = Field(max_length=36)
    reason: str | None = Field(None, max_length=300)


class CancelBooking(Tool):
    name = "cancel_booking"
    description = "Cancel a verified person's upcoming call. Confirm with them first."
    args_model = CancelArgs
    channels = PUBLIC
    requires_verification = True
    side_effect = True

    async def plan(self, ctx: ToolContext, args: CancelArgs, key: str) -> Planned:
        b, vc = await _booking_for(ctx, args.booking_id)
        b.status = BookingStatus.cancelled
        when = bk.display(b.starts_at, vc.contact.timezone or "Europe/London")
        consultant = await ctx.session.get(Consultant, b.consultant_id)
        return Planned(
            effects=[
                OutboxSpec("calendar.cancel_event", {"booking_id": str(b.id)}),
                OutboxSpec(
                    "email.send",
                    {
                        "to": vc.contact.email,
                        "subject": f"Your call on {when} is cancelled",
                        "body": "Hello,\n\nYour discovery call has been cancelled. You're welcome "
                        "to book another time in the website chat.\n\nAcme IT Consulting",
                    },
                ),
                OutboxSpec(
                    "slack.notify",
                    {
                        "text": f":x: Call with {vc.contact.name or vc.contact.email} on {when} "
                        f"cancelled ({consultant.name}). Reason: {args.reason or 'n/a'}",
                        "urgency": "normal",
                    },
                ),
            ],
            result={"cancelled": when},
        )


# ---------- Handover ----------


class HandoverArgs(BaseModel):
    reason: Literal[
        "pricing_negotiation",
        "complaint_or_legal",
        "technical_issue",
        "sensitive",
        "human_requested",
        "cannot_answer",
        "enterprise_escalation",
        "security_incident",
    ]
    summary: str = Field(
        min_length=10,
        max_length=800,
        description="Factual summary for the consultant: who, what, urgency",
    )


class RequestHandover(Tool):
    name = "request_handover"
    description = (
        "Pass the conversation to a human consultant with a summary. Use for pricing "
        "negotiation, complaints, legal or contract issues, urgent technical "
        "problems, sensitive topics, when they ask for a person, or when you can't "
        "help. You stop handling the conversation afterwards."
    )
    args_model = HandoverArgs
    channels = PUBLIC

    async def run(self, ctx: ToolContext, args: HandoverArgs) -> dict[str, Any]:
        h, _ = await handover.create_handover(
            ctx.session, ctx.conversation_id, args.reason, args.summary
        )
        hours = await config_store.get_value(ctx.session, "office.hours")
        return {
            "handover": h.ref,
            "tell_visitor": handover.follow_up_promise(hours, h.urgency)
            + f" Your reference is {h.ref}.",
        }


def client_tools(calendar: CalendarAdapter, tickets: TicketAdapter) -> list[Tool]:
    return [
        SendVerificationCode(),
        VerifyCode(),
        ListMyTickets(tickets),
        GetTicket(tickets),
        CreateTicket(tickets),
        AddTicketUpdate(),
        GetProjectUpdates(),
        ListMyBookings(),
        RescheduleBooking(calendar),
        CancelBooking(),
        RequestHandover(),
    ]
