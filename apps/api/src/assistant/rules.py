"""Schemas and previews for the rule settings staff edit in the dashboard.

Every rule set has a schema here, so a draft that would break the assistant (an unknown
company size, an end time before the start, a missing route) is refused when it's saved, not
discovered in production. Each also has a preview: "what would happen with these rules",
computed by the same code the assistant uses.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import holidays
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from assistant import handover, qualification
from assistant.booking import candidate_starts, display
from assistant.reminders import latest_allowed
from assistant.workdays import holiday_name, is_working_day

NEEDS = list(get_args(qualification.NeedCategory))
SIZES = list(get_args(qualification.CompanySize))
BUDGETS = list(get_args(qualification.BudgetRange))
TIMELINES = list(get_args(qualification.Timeline))
ROLES = list(get_args(qualification.Role))
REQUIRABLE = ["need_category", "company_size", "budget_range", "timeline", "role"]
URGENCIES = ["low", "normal", "high", "p1"]
HANDOVER_REASONS = [
    "security_incident",
    "pricing_negotiation",
    "complaint_or_legal",
    "technical_issue",
    "sensitive",
    "human_requested",
    "cannot_answer",
    "enterprise_escalation",
]
TIME = r"^([01]\d|2[0-3]):[0-5]\d$"


def _tz(v: str) -> str:
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown timezone '{v}'") from e
    return v


class _Days(BaseModel):
    days: list[int] = Field(min_length=1)
    start: str = Field(pattern=TIME)
    end: str = Field(pattern=TIME)
    holidays_country: str | None = None
    holidays_subdiv: str | None = None

    @field_validator("days")
    @classmethod
    def _weekdays(cls, v: list[int]) -> list[int]:
        if any(d not in range(7) for d in v):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("the end time must be after the start time")
        if self.holidays_country:
            try:
                holidays.country_holidays(self.holidays_country, subdiv=self.holidays_subdiv)
            except NotImplementedError as e:
                raise ValueError(
                    f"unknown holiday calendar {self.holidays_country}"
                    f"{'/' + self.holidays_subdiv if self.holidays_subdiv else ''}"
                ) from e
        return self


class OfficeHours(_Days):
    timezone: str

    _check_tz = field_validator("timezone")(_tz)


class MessagingHours(_Days):
    """Applied in each recipient's own timezone, so it has none of its own."""


class BookingRules(_Days):
    timezone: str
    duration_min: int = Field(ge=10, le=240)
    step_min: int = Field(ge=5, le=240)
    min_notice_hours: int = Field(ge=0, le=24 * 14)
    horizon_days: int = Field(ge=1, le=90)
    max_slots: int = Field(ge=1, le=20)
    max_per_day: int = Field(ge=1, le=20)
    consultant_skill: str = Field(min_length=1, max_length=60)

    _check_tz = field_validator("timezone")(_tz)


def _subset(allowed: list[str]):
    def check(v: list[str]) -> list[str]:
        bad = [x for x in v if x not in allowed]
        if bad:
            raise ValueError(f"unknown value(s): {', '.join(bad)}")
        return v

    return check


class QualificationRules(BaseModel):
    required: list[str]
    qualifying_needs: list[str]
    min_sizes: list[str]
    qualifying_budgets: list[str]
    unknown_budget_ok_for_roles: list[str]
    qualifying_timelines: list[str]
    enterprise_sizes: list[str]

    _r = field_validator("required")(_subset(REQUIRABLE))
    _n = field_validator("qualifying_needs")(_subset(NEEDS))
    _s = field_validator("min_sizes", "enterprise_sizes")(_subset(SIZES))
    _b = field_validator("qualifying_budgets")(_subset(BUDGETS))
    _o = field_validator("unknown_budget_ok_for_roles")(_subset(ROLES))
    _t = field_validator("qualifying_timelines")(_subset(TIMELINES))


class Route(BaseModel):
    slack: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    urgency: str

    @field_validator("urgency")
    @classmethod
    def _urgency(cls, v: str) -> str:
        if v not in URGENCIES:
            raise ValueError(f"urgency is one of {', '.join(URGENCIES)}")
        return v


class HandoverRules(BaseModel):
    security_keywords: list[str] = Field(min_length=1)
    security_exclusions: list[str] = []
    routes: dict[str, Route]

    @field_validator("security_keywords", "security_exclusions")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return [x.strip() for x in v if x.strip()]

    @field_validator("routes")
    @classmethod
    def _all_routes(cls, v: dict[str, Route]) -> dict[str, Route]:
        missing = [r for r in HANDOVER_REASONS if r not in v]
        if missing:
            raise ValueError(f"every reason needs a route; missing: {', '.join(missing)}")
        return v


SCHEMAS: dict[str, type[BaseModel]] = {
    "qualification.rules": QualificationRules,
    "handover.rules": HandoverRules,
    "booking.rules": BookingRules,
    "office.hours": OfficeHours,
    "messaging.hours": MessagingHours,
}


def validate(key: str, value: Any) -> Any:
    """The cleaned value, or ValueError with a message staff can act on."""
    schema = SCHEMAS.get(key)
    if schema is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("these settings must be a set of fields, not plain text")
    try:
        return {**value, **schema.model_validate(value).model_dump(exclude_unset=False)}
    except ValidationError as e:
        msgs = []
        for err in e.errors(include_url=False):
            where = ".".join(str(p) for p in err["loc"])
            msg = err["msg"].removeprefix("Value error, ")
            msgs.append(f"{where}: {msg}" if where else msg)
        raise ValueError("; ".join(msgs)) from e


# ---------- Previews ----------


def preview(key: str, value: dict[str, Any], sample: dict[str, Any], now: datetime | None = None):
    now = now or datetime.now(UTC)
    if key == "qualification.rules":
        profile = {k: v for k, v in sample.items() if k in REQUIRABLE and v}
        a = qualification.assess(profile, value)
        return {"status": a.status, "missing": a.missing, "reasons": a.reasons}
    if key == "handover.rules":
        text = str(sample.get("text") or "")
        trigger = handover.detect_trigger(text, value)
        route = value["routes"].get(trigger) if trigger else None
        return {"trigger": trigger, "route": route}
    if key == "office.hours":
        tz = ZoneInfo(value["timezone"])
        local = now.astimezone(tz)
        return {
            "now_local": f"{local:%A %H:%M} ({value['timezone']})",
            "open_now": _inside(local, value),
            "promise": handover.follow_up_promise(value, "normal", now),
            "upcoming_closures": _closures(local.date(), value),
        }
    if key == "messaging.hours":
        tz_name = str(sample.get("timezone") or "Europe/London")
        _tz(tz_name)
        local = now.astimezone(ZoneInfo(tz_name))
        sent = latest_allowed(now, value, tz_name).astimezone(ZoneInfo(tz_name))
        return {
            "now_local": f"{local:%A %H:%M} ({tz_name})",
            "allowed_now": _inside(local, value),
            # Reminders move *earlier* into the last allowed window, never later.
            "reminder_due_now_sent_at": f"{sent:%A %d %B, %H:%M}",
            "upcoming_closures": _closures(local.date(), value),
        }
    if key == "booking.rules":
        tz = value["timezone"]
        starts = candidate_starts(value, now)
        per_day: dict[Any, int] = {}
        shown = []
        for s in starts:
            d = s.astimezone(ZoneInfo(tz)).date()
            if per_day.get(d, 0) >= value["max_per_day"]:
                continue
            per_day[d] = per_day.get(d, 0) + 1
            shown.append(display(s, tz))
            if len(shown) >= value["max_slots"]:
                break
        return {"slots": shown, "candidates_in_window": len(starts)}
    raise KeyError(key)


def _inside(local: datetime, hours: dict[str, Any]) -> bool:
    return is_working_day(local.date(), hours) and hours["start"] <= f"{local:%H:%M}" < hours["end"]


def _closures(start, hours: dict[str, Any], days: int = 60) -> list[str]:
    """Working weekdays in the next `days` that are closed for a public holiday."""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        name = holiday_name(d, hours)
        if name and d.weekday() in hours["days"]:
            out.append(f"{d:%a %d %b}: {name}")
    return out[:5]
