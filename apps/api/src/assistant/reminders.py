"""Booking confirmations and reminders (decided 2026-11-16):

| Message      | Client / visitor        | Consultant (team)   |
|--------------|-------------------------|---------------------|
| Confirmation | email, at booking       | Slack, at booking   |
| Reminder     | email, 24 hours before  | Slack, 1 hour before|

Reminders are ordinary outbox items scheduled with `not_before`, so the worker sends them
when due. They carry the booking's start time; if the booking was cancelled or moved, the
handler skips them. Client emails respect `messaging.hours` in the client's timezone.
"""

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from assistant.booking import display
from assistant.models import Booking, Consultant, Contact
from assistant.tools.registry import OutboxSpec
from assistant.workdays import is_working_day

CLIENT_LEAD = timedelta(hours=24)
CONSULTANT_LEAD = timedelta(hours=1)


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def latest_allowed(at: datetime, hours: dict[str, Any], tz_name: str) -> datetime:
    """`at` if it's inside the recipient's messaging hours, else the latest allowed moment
    before it (the end of the previous allowed window)."""
    tz = ZoneInfo(tz_name)
    local = at.astimezone(tz)
    start, end = _hm(hours["start"]), _hm(hours["end"])
    if is_working_day(local.date(), hours) and start <= local.time() < end:
        return at
    day = local.date() if local.time() >= end else local.date() - timedelta(days=1)
    for _ in range(14):  # enough to cross Christmas plus a weekend
        if is_working_day(day, hours):
            return datetime.combine(day, end, tz) - timedelta(minutes=5)
        day -= timedelta(days=1)
    return at


def booking_messages(
    booking: Booking,
    contact: Contact | None,
    consultant: Consultant,
    visitor_tz: str,
    hours: dict[str, Any],
    now: datetime,
) -> list[OutboxSpec]:
    """Consultant confirmation now, plus both reminders (when they're still in the future)."""
    when_team = display(booking.starts_at, "Europe/London")
    who = f"{contact.name or contact.email}" if contact else "a visitor"
    mention = f"<@{consultant.slack_user_id}> " if consultant.slack_user_id else ""
    base = {"booking_id": str(booking.id), "starts_at": booking.starts_at.isoformat()}
    effects = [
        OutboxSpec(
            "slack.notify",
            {
                "text": f":calendar: {mention}New discovery call with *{who}*"
                f"{' (' + contact.email + ')' if contact and contact.email else ''}: "
                f"{when_team}. Topic: {booking.topic or 'n/a'}. Consultant: {consultant.name}",
                "urgency": "normal",
            },
        )
    ]

    consultant_at = booking.starts_at - CONSULTANT_LEAD
    if consultant_at > now:
        effects.append(
            OutboxSpec(
                "booking.reminder", {**base, "audience": "consultant"}, not_before=consultant_at
            )
        )
    if contact and contact.email:
        client_at = latest_allowed(booking.starts_at - CLIENT_LEAD, hours, visitor_tz)
        if client_at > now:
            effects.append(
                OutboxSpec(
                    "booking.reminder",
                    {**base, "audience": "client", "timezone": visitor_tz},
                    not_before=client_at,
                )
            )
    return effects
