"""Human handover: decide who takes over, tell them (Slack + email), tell the visitor when.

Triggers:
* Rule-based, before the model runs: security-incident keywords -> P1 to security on-call.
  The assistant keeps going only to collect contact details.
* Model-initiated via the `request_handover` tool: pricing negotiation, complaints/legal,
  urgent technical issues, sensitive topics, "I want a person", repeated "I don't know".
  The conversation is then marked handed over and the assistant steps aside.

Routing comes from config `handover.rules`. Enterprise accounts go to their account manager.
"""

import re
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant import audit, config_store
from assistant.models import (
    Company,
    Consultant,
    Contact,
    Conversation,
    ConversationStatus,
    Handover,
    HandoverStatus,
    Lead,
    Message,
    MessageRole,
)
from assistant.settings import get_settings
from assistant.tools.registry import enqueue
from assistant.workdays import holiday_name, is_working_day

REASONS = (
    "security_incident",
    "pricing_negotiation",
    "complaint_or_legal",
    "technical_issue",
    "sensitive",
    "human_requested",
    "cannot_answer",
    "enterprise_escalation",
)

_ICON = {
    "p1": ":rotating_light: P1",
    "high": ":red_circle: High",
    "normal": ":large_blue_circle:",
    "low": ":white_circle:",
}


def detect_trigger(text: str, rules: dict[str, Any]) -> str | None:
    t = (text or "").lower()
    for phrase in rules.get("security_exclusions", []):
        t = t.replace(phrase.lower(), " ")
    for kw in rules.get("security_keywords", []):
        if re.search(rf"\b{re.escape(kw.lower())}\b", t):
            return "security_incident"
    return None


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def follow_up_promise(hours: dict[str, Any], urgency: str, now: datetime | None = None) -> str:
    """What to tell the visitor about when a person will get back to them."""
    if urgency == "p1":
        return "Our security team has been alerted and will contact you as a priority."
    tz = ZoneInfo(hours["timezone"])
    local = (now or datetime.now(UTC)).astimezone(tz)
    working_today = is_working_day(local.date(), hours)
    if working_today and _hm(hours["start"]) <= local.time() < _hm(hours["end"]):
        return "A consultant will get back to you shortly, during today's office hours."
    if working_today and local.time() < _hm(hours["start"]):
        return (
            f"Our office opens at {hours['start']} ({hours['timezone']}) today; a consultant "
            "will get back to you then."
        )
    nxt = local.date() + timedelta(days=1)
    for _ in range(14):  # skip weekends and public holidays
        if is_working_day(nxt, hours):
            break
        nxt += timedelta(days=1)
    closed = holiday_name(local.date(), hours)
    reason = (
        f"Our office is closed today ({closed})" if closed else "Our office is closed right now"
    )
    return (
        f"{reason}; a consultant will get back to you on {nxt:%A} {nxt.day} {nxt:%B} from "
        f"{hours['start']} ({hours['timezone']})."
    )


async def _transcript_excerpt(session: AsyncSession, conversation_id: uuid.UUID, n: int = 6) -> str:
    rows = (
        await session.execute(
            select(Message.role, Message.content)
            .where(
                Message.conversation_id == conversation_id,
                Message.role.in_([MessageRole.user, MessageRole.assistant]),
                Message.content.is_not(None),
            )
            .order_by(Message.id.desc())
            .limit(n)
        )
    ).all()
    return "\n".join(
        f"{'Visitor' if r == 'user' else 'Assistant'}: {c[:300]}" for r, c in reversed(rows)
    )


async def create_handover(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    reason: str,
    summary: str,
    *,
    urgency: str | None = None,
    step_aside: bool = True,
) -> tuple[Handover, bool]:
    """Create (or reuse an open) handover. Returns (handover, created)."""
    existing = (
        await session.execute(
            select(Handover).where(
                Handover.conversation_id == conversation_id,
                Handover.reason == reason,
                Handover.status == HandoverStatus.open,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing, False

    rules = await config_store.get_value(session, "handover.rules")
    conv = await session.get(Conversation, conversation_id)
    contact = await session.get(Contact, conv.contact_id) if conv.contact_id else None
    company = (
        await session.get(Company, contact.company_id) if contact and contact.company_id else None
    )
    route = dict(rules["routes"].get(reason) or rules["routes"]["human_requested"])
    urgency = urgency or route["urgency"]

    owner_note = ""
    if (
        company
        and company.is_enterprise
        and company.account_manager_id
        and reason != "security_incident"
    ):
        am = await session.get(Consultant, company.account_manager_id)
        if am:  # enterprise accounts go straight to their account manager
            route["email"] = am.email
            owner_note = f" (account manager: {am.name})"
            if urgency in ("low", "normal"):
                urgency = "high"

    lead = (
        await session.execute(select(Lead).where(Lead.conversation_id == conversation_id))
    ).scalar_one_or_none()
    s = get_settings()
    link = f"{s.dashboard_url}/conversations/{conversation_id}"
    who = (
        f"{contact.name or 'Unknown name'} <{contact.email or 'no email'}>"
        f"{' · ' + contact.phone if contact and contact.phone else ''}"
        if contact
        else "Anonymous visitor (no contact details yet)"
    )
    detail = "\n".join(
        filter(
            None,
            [
                f"Who: {who}",
                f"Company: {company.name}{' (enterprise)' if company.is_enterprise else ''}"
                if company
                else None,
                f"Verified client: {'yes' if conv.verified else 'no'}",
                f"Reason: {reason.replace('_', ' ')}",
                f"Summary: {summary}",
                f"Lead: {lead.status}" if lead else None,
                "",
                "Recent messages:",
                await _transcript_excerpt(session, conversation_id),
                "",
                f"Full transcript: {link}",
            ],
        )
    )

    h = Handover(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        contact_id=contact.id if contact else None,
        reason=reason,
        urgency=urgency,
        route_to=f"{route['slack']} / {route['email']}",
        summary=summary,
        status=HandoverStatus.open,
    )
    session.add(h)
    await session.flush()  # assigns h.number

    await enqueue(
        session,
        "slack.notify",
        {
            "text": f"{_ICON.get(urgency, '')} *Handover {h.ref}* → {route['slack']}{owner_note}\n"
            f"{detail}",
            "urgency": "high" if urgency in ("p1", "high") else "normal",
            "conversation_id": str(conversation_id),
        },
        key=f"handover:{h.id}:slack",
    )
    await enqueue(
        session,
        "email.send",
        {
            "to": route["email"],
            "subject": f"[{urgency.upper()}] Handover {h.ref}: {reason.replace('_', ' ')}",
            "body": detail,
        },
        key=f"handover:{h.id}:email",
    )

    if step_aside:
        conv.status = ConversationStatus.handed_over
    audit.record(
        session,
        actor_type="system",
        actor_id="assistant",
        action="handover.created",
        target_type="conversation",
        target_id=str(conversation_id),
        handover=h.ref,
        reason=reason,
        urgency=urgency,
    )
    await session.flush()
    return h, True


async def acknowledge(session: AsyncSession, handover_id: uuid.UUID, staff_sub: str) -> Handover:
    h = await session.get(Handover, handover_id, with_for_update=True)
    if h is None:
        raise LookupError("handover not found")
    if h.status == HandoverStatus.open:
        h.status = HandoverStatus.acknowledged
        h.acknowledged_at = datetime.now(UTC)
        h.acknowledged_by = staff_sub
    return h
