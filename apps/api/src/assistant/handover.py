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
from assistant.auth import StaffUser
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
    assigned_to: str | None = None  # None = the general team queue
    if (
        company
        and company.is_enterprise
        and company.account_manager_id
        and reason != "security_incident"
    ):
        am = await session.get(Consultant, company.account_manager_id)
        if am:  # enterprise accounts go straight to their account manager
            route["email"] = am.email
            assigned_to = am.email.lower()
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
        assigned_to=assigned_to,
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


class HandoverRefused(Exception):
    """The staff member may not do this to this handover (message is shown to them)."""


def may_take(h: Handover, user: StaffUser) -> bool:
    """Managers and admins take anything. Consultants take the team queue, or handovers
    assigned to them, never someone else's (e.g. another account manager's client)."""
    if user.has_any("admin", "manager"):
        return True
    return h.assigned_to is None or h.assigned_to == (user.email or "").lower()


async def _locked(session: AsyncSession, handover_id: uuid.UUID) -> Handover:
    h = await session.get(Handover, handover_id, with_for_update=True)
    if h is None:
        raise LookupError("handover not found")
    return h


async def acknowledge(session: AsyncSession, handover_id: uuid.UUID, user: StaffUser) -> Handover:
    """'I'll take it'. The first person wins; later clicks don't change the owner."""
    h = await _locked(session, handover_id)
    if h.status == HandoverStatus.open:
        if not may_take(h, user):
            raise HandoverRefused(
                f"This handover is for {h.assigned_to}. Ask a manager to reassign it."
            )
        h.status = HandoverStatus.acknowledged
        h.acknowledged_at = datetime.now(UTC)
        h.acknowledged_by = user.sub
        h.acknowledged_by_name = user.display_name
        # Tell the channel, so nobody else starts on it.
        await _team_note(session, h, f"Handover {h.ref} taken by {user.display_name}.", "taken")
    return h


async def _team_note(session: AsyncSession, h: Handover, text: str, event: str) -> None:
    """Slack follow-up in the handover's channel, via the outbox (never sent twice)."""
    channel = h.route_to.split(" / ")[0]
    when = (h.resolved_at if event == "resolved" else h.acknowledged_at) or datetime.now(UTC)
    await enqueue(
        session,
        "slack.notify",
        {
            "text": f"{text} ({channel})",
            "urgency": "normal",
            "conversation_id": str(h.conversation_id),
        },
        # The time makes a second take (after a reassign) its own message.
        key=f"handover:{h.id}:{event}:{when.isoformat()}",
    )


async def resolve(session: AsyncSession, handover_id: uuid.UUID, user: StaffUser) -> Handover:
    """Done. Managers can close any; consultants close the ones they took, or (if nobody
    has taken it yet) ones they're allowed to take."""
    h = await _locked(session, handover_id)
    if h.status == HandoverStatus.resolved:
        return h
    taken_by_me = h.acknowledged_by == user.sub
    untaken = h.status == HandoverStatus.open
    if not (user.has_any("admin", "manager") or taken_by_me or (untaken and may_take(h, user))):
        who = h.acknowledged_by_name or h.assigned_to or "someone else"
        raise HandoverRefused(f"{who} is handling this one.")
    if untaken:  # resolved without being taken first: record who dealt with it
        h.acknowledged_at = datetime.now(UTC)
        h.acknowledged_by = user.sub
        h.acknowledged_by_name = user.display_name
    h.status = HandoverStatus.resolved
    h.resolved_at = datetime.now(UTC)
    h.resolved_by_name = user.display_name
    await session.flush()
    # Once nothing about this conversation is still waiting for a person, the assistant
    # answers it again (until then it only says a consultant is looking after it).
    still_open = (
        await session.execute(
            select(Handover.id).where(
                Handover.conversation_id == h.conversation_id,
                Handover.status != HandoverStatus.resolved,
            )
        )
    ).first()
    conv = await session.get(Conversation, h.conversation_id)
    resumed = (
        still_open is None and conv is not None and conv.status == ConversationStatus.handed_over
    )
    if resumed:
        conv.status = ConversationStatus.active
    await _team_note(
        session,
        h,
        f"Handover {h.ref} resolved by {user.display_name}."
        + (" The assistant is answering this conversation again." if resumed else ""),
        "resolved",
    )
    return h


async def reassign(
    session: AsyncSession, handover_id: uuid.UUID, user: StaffUser, to_email: str | None
) -> Handover:
    """Managers and admins pass a handover to a named person (or back to the team queue).
    It goes back to 'open' for them to take, and they're told in Slack."""
    if not user.has_any("admin", "manager"):
        raise HandoverRefused("Only managers and admins can reassign handovers.")
    h = await _locked(session, handover_id)
    if h.status == HandoverStatus.resolved:
        raise HandoverRefused("This handover is already resolved.")
    h.assigned_to = to_email.strip().lower() if to_email else None
    h.status = HandoverStatus.open
    h.acknowledged_at = h.acknowledged_by = h.acknowledged_by_name = None
    target = h.assigned_to or "the team queue"
    await enqueue(
        session,
        "slack.notify",
        {
            "text": f"Handover {h.ref} was reassigned to {target} by {user.display_name}.",
            "urgency": "high" if h.urgency in ("p1", "high") else "normal",
            "conversation_id": str(h.conversation_id),
        },
        key=f"handover:{h.id}:reassign:{uuid.uuid4()}",
    )
    return h
