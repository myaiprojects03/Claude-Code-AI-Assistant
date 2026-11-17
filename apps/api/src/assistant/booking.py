"""Discovery-call availability and reservation.

Slots come from config (`booking.rules`): working days/hours in the consultants' timezone,
minimum notice and a booking horizon. A slot is offered if at least one consultant with the
discovery skill is free in our DB (and in Google Calendar, when connected).

Reserving re-validates the slot against the rules (the model can't book arbitrary times) and
inserts the booking; the DB exclusion constraint makes double-booking impossible even under
concurrent requests, in which case the next free consultant is tried.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.integrations.calendar import CalendarAdapter
from assistant.models import Booking, BookingStatus, Consultant
from assistant.workdays import is_working_day


@dataclass
class Slot:
    start: datetime  # UTC
    end: datetime
    consultant_id: uuid.UUID


class SlotUnavailable(Exception):
    pass


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def candidate_starts(
    rules: dict[str, Any], now: datetime, earliest: date | None = None
) -> list[datetime]:
    """All rule-compliant slot starts (UTC) in the booking window, in order."""
    tz = ZoneInfo(rules["timezone"])
    first = now + timedelta(hours=rules["min_notice_hours"])
    last = now + timedelta(days=rules["horizon_days"])
    step = timedelta(minutes=rules["step_min"])
    dur = timedelta(minutes=rules["duration_min"])
    day = max(first.astimezone(tz).date(), earliest or date.min)
    out: list[datetime] = []
    while day <= last.astimezone(tz).date():
        if is_working_day(day, rules):
            t = datetime.combine(day, _hm(rules["start"]), tz)
            end_of_day = datetime.combine(day, _hm(rules["end"]), tz)
            while t + dur <= end_of_day:
                if first <= t <= last:
                    out.append(t.astimezone(UTC))
                t += step
        day += timedelta(days=1)
    return out


async def _consultants(session: AsyncSession, skill: str) -> list[Consultant]:
    return list(
        (
            await session.execute(
                select(Consultant)
                .where(Consultant.active.is_(True), Consultant.skills.contains([skill]))
                .order_by(Consultant.email)
            )
        ).scalars()
    )


async def _busy_map(
    session: AsyncSession,
    calendar: CalendarAdapter,
    consultants: list[Consultant],
    start: datetime,
    end: datetime,
) -> dict[uuid.UUID, list[tuple[datetime, datetime]]]:
    busy: dict[uuid.UUID, list[tuple[datetime, datetime]]] = defaultdict(list)
    rows = await session.execute(
        select(Booking.consultant_id, Booking.starts_at, Booking.ends_at).where(
            Booking.consultant_id.in_([c.id for c in consultants]),
            Booking.status != BookingStatus.cancelled,
            Booking.ends_at > start,
            Booking.starts_at < end,
        )
    )
    for cid, s, e in rows:
        busy[cid].append((s, e))
    for c in consultants:
        if c.calendar_id:
            busy[c.id] += await calendar.busy(c.calendar_id, start, end)
    return busy


def _free(busy: list[tuple[datetime, datetime]], s: datetime, e: datetime) -> bool:
    return all(not (bs < e and s < be) for bs, be in busy)


async def available_slots(
    session: AsyncSession,
    calendar: CalendarAdapter,
    rules: dict[str, Any],
    *,
    now: datetime | None = None,
    earliest: date | None = None,
) -> list[Slot]:
    now = now or datetime.now(UTC)
    consultants = await _consultants(session, rules["consultant_skill"])
    starts = candidate_starts(rules, now, earliest)
    if not consultants or not starts:
        return []
    dur = timedelta(minutes=rules["duration_min"])
    busy = await _busy_map(session, calendar, consultants, starts[0], starts[-1] + dur)
    load = {c.id: len(busy[c.id]) for c in consultants}
    tz = ZoneInfo(rules["timezone"])
    per_day: dict[date, int] = defaultdict(int)
    slots: list[Slot] = []
    for s in starts:
        d = s.astimezone(tz).date()
        if per_day[d] >= rules["max_per_day"]:
            continue
        free = [c for c in consultants if _free(busy[c.id], s, s + dur)]
        if not free:
            continue
        pick = min(free, key=lambda c: load[c.id])  # spread work across consultants
        slots.append(Slot(s, s + dur, pick.id))
        per_day[d] += 1
        if len(slots) >= rules["max_slots"]:
            break
    return slots


async def reserve(
    session: AsyncSession,
    calendar: CalendarAdapter,
    rules: dict[str, Any],
    start: datetime,
    *,
    key: str,
    contact_id: uuid.UUID | None,
    conversation_id: uuid.UUID,
    topic: str | None,
    now: datetime | None = None,
) -> Booking:
    now = now or datetime.now(UTC)
    start = start.astimezone(UTC)
    if start not in candidate_starts(rules, now):
        raise SlotUnavailable("that time isn't an available discovery-call slot")
    end = start + timedelta(minutes=rules["duration_min"])
    consultants = await _consultants(session, rules["consultant_skill"])
    busy = await _busy_map(session, calendar, consultants, start, end)
    for c in sorted(consultants, key=lambda c: len(busy[c.id])):
        if not _free(busy[c.id], start, end):
            continue
        booking = Booking(
            id=uuid.uuid4(),
            consultant_id=c.id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            starts_at=start,
            ends_at=end,
            status=BookingStatus.pending,
            topic=topic,
            idempotency_key=key,
        )
        try:
            async with session.begin_nested():
                session.add(booking)
                await session.flush()
            return booking
        except IntegrityError:  # someone booked this consultant concurrently: try the next
            continue
    raise SlotUnavailable("that time was just taken")


def display(dt: datetime, tz_name: str) -> str:
    local = dt.astimezone(ZoneInfo(tz_name))
    return f"{local:%A} {local.day} {local:%B}, {local:%H:%M} ({tz_name})"
