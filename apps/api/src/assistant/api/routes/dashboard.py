"""APIs for the staff dashboard (Phase 6). Config writes reuse the admin routes."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from assistant import audit, config_store
from assistant import rules as rules_schema
from assistant.auth import StaffUser, require_roles
from assistant.db import get_session
from assistant.kb import public as public_kb
from assistant.models import (
    Booking,
    BookingStatus,
    ClientAccess,
    Company,
    ConfigStatus,
    ConfigVersion,
    Consultant,
    Contact,
    Conversation,
    Corpus,
    Document,
    Handover,
    HandoverStatus,
    Lead,
    Message,
    MessageRole,
    PublicChunk,
    Ticket,
)
from assistant.settings import get_settings

log = structlog.get_logger()
router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])
staff_any = require_roles("admin", "manager", "consultant")
managers = require_roles("admin", "manager")

CLOSED_TICKET = ("Done", "Closed", "Resolved")
NO_RESULTS = '{"results": []'  # how search_knowledge's empty result is stored
# Config keys staff may edit from the dashboard (prompts stay code-reviewed for now).
EDITABLE_CONFIG = {
    "chat.greeting": "Greeting shown when a visitor opens the chat",
    "chat.fallback_error": "Reply when the model is unavailable",
    "guardrail.fallback": "Reply that replaces a blocked answer (prices, promises)",
    "qualification.rules": "Lead qualification rules",
    "booking.rules": "Discovery-call availability rules",
    "handover.rules": "Handover keywords and routing",
    "office.hours": "Office hours (used for follow-up promises)",
    "messaging.hours": "When client emails may be sent",
}


def _matches(q: str, *cols):
    """Case-insensitive 'contains' across columns. % and _ in the search are literal."""
    term = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{term}%"
    return or_(*(func.lower(func.coalesce(c, "")).like(like, escape="\\") for c in cols))


# ---------- Session ----------


class Me(BaseModel):
    sub: str
    username: str | None
    email: str | None
    roles: list[str]


@router.get("/me", response_model=Me)
async def me(user: StaffUser = Depends(staff_any)):
    return Me(sub=user.sub, username=user.username, email=user.email, roles=sorted(user.roles))


# ---------- Overview ----------


class Overview(BaseModel):
    generated_at: datetime
    conversations_7d: int
    conversations_prev_7d: int
    leads_30d: dict[str, int]
    qualified_rate_30d: float | None
    # Qualified/enterprise leads (30 days) that went on to book a call.
    booking_conversion_30d: float | None
    qualified_leads_30d: int
    # Visitor conversations (30 days) handed over to a person.
    escalation_rate_30d: float | None
    conversations_30d: int
    unanswered_30d: int  # questions the public content had nothing for
    bookings_upcoming: int
    bookings_next_7d: int
    handovers_open: dict[str, int]
    tickets_open: int
    guardrail_replacements_7d: int
    llm_tokens_today: int
    llm_calls_today: int
    degraded_replies_7d: int
    daily_conversations: list[dict[str, Any]]


@router.get("/overview", response_model=Overview)
async def overview(
    user: StaffUser = Depends(staff_any), session: AsyncSession = Depends(get_session)
):
    now = datetime.now(UTC)
    d7, d14, d30 = now - timedelta(days=7), now - timedelta(days=14), now - timedelta(days=30)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async def scalar(q):
        return (await session.execute(q)).scalar_one() or 0

    conv7 = await scalar(
        select(func.count()).where(
            Conversation.created_at >= d7, Conversation.channel != "internal"
        )
    )
    conv_prev = await scalar(
        select(func.count()).where(
            Conversation.created_at >= d14,
            Conversation.created_at < d7,
            Conversation.channel != "internal",
        )
    )
    leads = dict(
        (
            await session.execute(
                select(Lead.status, func.count())
                .where(Lead.created_at >= d30)
                .group_by(Lead.status)
            )
        ).all()
    )
    decided = sum(leads.get(s, 0) for s in ("qualified", "enterprise", "unqualified"))
    q_rate = (leads.get("qualified", 0) + leads.get("enterprise", 0)) / decided if decided else None
    booked = (
        select(Booking.id)
        .where(
            Booking.status != BookingStatus.cancelled,
            (Booking.conversation_id == Lead.conversation_id)
            | (Booking.contact_id == Lead.contact_id),
        )
        .exists()
    )
    qualified_n, booked_n = (
        await session.execute(
            select(func.count(), func.count().filter(booked))
            .select_from(Lead)
            .where(Lead.created_at >= d30, Lead.status.in_(("qualified", "enterprise")))
        )
    ).one()
    conv30, escalated = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(
                    select(Handover.id).where(Handover.conversation_id == Conversation.id).exists()
                ),
            ).where(Conversation.created_at >= d30, Conversation.channel != "internal")
        )
    ).one()
    unanswered_n = await scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.role == MessageRole.tool,
            Message.tool_name == "search_knowledge",
            Message.content.startswith(NO_RESULTS),
            Message.created_at >= d30,
        )
    )
    upcoming = await scalar(
        select(func.count()).where(
            Booking.starts_at > now, Booking.status != BookingStatus.cancelled
        )
    )
    next7 = await scalar(
        select(func.count()).where(
            Booking.starts_at > now,
            Booking.starts_at < now + timedelta(days=7),
            Booking.status != BookingStatus.cancelled,
        )
    )
    handovers = dict(
        (
            await session.execute(
                select(Handover.urgency, func.count())
                .where(Handover.status == HandoverStatus.open)
                .group_by(Handover.urgency)
            )
        ).all()
    )
    tickets_open = await scalar(
        select(func.count()).select_from(Ticket).where(Ticket.status.not_in(CLOSED_TICKET))
    )
    tokens, calls = (
        await session.execute(
            select(
                func.coalesce(func.sum(Message.tokens_in + Message.tokens_out), 0), func.count()
            ).where(
                Message.created_at >= midnight,
                Message.role == MessageRole.assistant,
                Message.tokens_in.is_not(None),
            )
        )
    ).one()
    degraded = await scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.created_at >= d7,
            Message.role == MessageRole.assistant,
            Message.provider == "none",
        )
    )
    # Guardrail replacements: stored replies equal to the configured fallback text.
    fallback = await config_store.get_value(session, "guardrail.fallback")
    guarded = await scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.created_at >= d7,
            Message.role == MessageRole.assistant,
            Message.content == fallback,
        )
    )

    day = func.date_trunc("day", Conversation.created_at)
    rows = dict(
        (
            await session.execute(
                select(day, func.count())
                .where(Conversation.created_at >= d14, Conversation.channel != "internal")
                .group_by(day)
            )
        ).all()
    )
    daily = []
    for i in range(13, -1, -1):
        dt = midnight - timedelta(days=i)
        count = next((c for k, c in rows.items() if k.date() == dt.date()), 0)
        daily.append({"date": dt.date().isoformat(), "count": count})

    return Overview(
        generated_at=now,
        conversations_7d=conv7,
        conversations_prev_7d=conv_prev,
        leads_30d=leads,
        qualified_rate_30d=q_rate,
        booking_conversion_30d=booked_n / qualified_n if qualified_n else None,
        qualified_leads_30d=qualified_n,
        escalation_rate_30d=escalated / conv30 if conv30 else None,
        conversations_30d=conv30,
        unanswered_30d=unanswered_n,
        bookings_upcoming=upcoming,
        bookings_next_7d=next7,
        handovers_open=handovers,
        tickets_open=tickets_open,
        guardrail_replacements_7d=guarded,
        llm_tokens_today=int(tokens),
        llm_calls_today=calls,
        degraded_replies_7d=degraded,
        daily_conversations=daily,
    )


# ---------- Conversations ----------


class ConversationRow(BaseModel):
    id: uuid.UUID
    channel: str
    status: str
    verified: bool
    created_at: datetime
    updated_at: datetime
    contact_name: str | None
    contact_email: str | None
    company: str | None
    lead_status: str | None
    messages: int
    last_message: str | None


@router.get("/conversations", response_model=list[ConversationRow])
async def conversations(
    channel: str | None = None,
    status_: str | None = Query(None, alias="status"),
    q: str | None = Query(None, max_length=100, description="Search contact name/email"),
    limit: int = Query(50, le=200),
    user: StaffUser = Depends(managers),
    session: AsyncSession = Depends(get_session),
):
    counts = (
        select(
            Message.conversation_id, func.count().label("n"), func.max(Message.id).label("last_id")
        )
        .where(Message.role.in_([MessageRole.user, MessageRole.assistant]))
        .group_by(Message.conversation_id)
        .subquery()
    )
    last = select(Message.content).where(Message.id == counts.c.last_id).scalar_subquery()
    stmt = (
        select(
            Conversation,
            Contact.name,
            Contact.email,
            Company.name,
            Lead.status,
            func.coalesce(counts.c.n, 0),
            last,
        )
        .outerjoin(Contact, Contact.id == Conversation.contact_id)
        .outerjoin(Company, Company.id == Contact.company_id)
        .outerjoin(Lead, Lead.conversation_id == Conversation.id)
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .where(Conversation.channel != "internal")  # private to their author
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if status_:
        stmt = stmt.where(Conversation.status == status_)
    if q:
        # Same escaping as leads/bookings: "%" and "_" are literal (Phase 7 finding).
        stmt = stmt.where(_matches(q, Contact.name, Contact.email))
    rows = (await session.execute(stmt)).all()
    return [
        ConversationRow(
            id=c.id,
            channel=c.channel,
            status=c.status,
            verified=c.verified,
            created_at=c.created_at,
            updated_at=c.updated_at,
            contact_name=name,
            contact_email=email,
            company=company,
            lead_status=lead,
            messages=n,
            last_message=(lm or "")[:160] or None,
        )
        for c, name, email, company, lead, n, lm in rows
    ]


# ---------- Leads ----------


class LeadRow(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    status: str
    name: str | None
    email: str | None
    company: str | None
    need: str | None
    need_category: str | None
    company_size: str | None
    budget_range: str | None
    timeline: str | None
    role: str | None
    summary: str | None
    created_at: datetime


@router.get("/leads", response_model=list[LeadRow])
async def leads(
    status_: str | None = Query(None, alias="status"),
    q: str | None = Query(None, max_length=100, description="Name, email, company or need"),
    limit: int = Query(100, le=500),
    user: StaffUser = Depends(managers),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Lead, Contact.name, Contact.email, Company.name)
        .outerjoin(Contact, Contact.id == Lead.contact_id)
        .outerjoin(Company, Company.id == Contact.company_id)
        .order_by(Lead.created_at.desc())
        .limit(limit)
    )
    if status_:
        stmt = stmt.where(Lead.status == status_)
    if q and q.strip():
        stmt = stmt.where(
            _matches(q, Contact.name, Contact.email, Company.name, Lead.need, Lead.summary)
        )
    rows = (await session.execute(stmt)).all()
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="leads.list",
        target_type="leads",
        target_id=status_ or "all",
    )
    await session.commit()
    return [
        LeadRow(
            id=lead.id,
            conversation_id=lead.conversation_id,
            status=lead.status,
            name=n,
            email=e,
            company=co,
            need=lead.need,
            need_category=lead.need_category,
            company_size=lead.company_size,
            budget_range=lead.budget_range,
            timeline=lead.timeline,
            role=lead.role,
            summary=lead.summary,
            created_at=lead.created_at,
        )
        for lead, n, e, co in rows
    ]


# ---------- Bookings ----------


class BookingRow(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: str
    topic: str | None
    consultant: str
    contact_name: str | None
    contact_email: str | None
    contact_timezone: str | None  # the visitor picked the time in this timezone
    company: str | None
    conversation_id: uuid.UUID | None
    calendar_event_id: str | None


@router.get("/bookings", response_model=list[BookingRow])
async def bookings(
    scope: str = Query("upcoming", pattern="^(upcoming|past|all)$"),
    include_cancelled: bool = False,
    q: str | None = Query(None, max_length=100, description="Visitor, company, topic, consultant"),
    limit: int = Query(100, le=500),
    user: StaffUser = Depends(staff_any),
    session: AsyncSession = Depends(get_session),
):
    now = datetime.now(UTC)
    stmt = (
        select(
            Booking, Consultant.name, Contact.name, Contact.email, Company.name, Contact.timezone
        )
        .join(Consultant, Consultant.id == Booking.consultant_id)
        .outerjoin(Contact, Contact.id == Booking.contact_id)
        .outerjoin(Company, Company.id == Contact.company_id)
        .limit(limit)
    )
    if scope == "upcoming":
        stmt = stmt.where(Booking.starts_at >= now).order_by(Booking.starts_at)
    elif scope == "past":
        stmt = stmt.where(Booking.starts_at < now).order_by(Booking.starts_at.desc())
    else:
        stmt = stmt.order_by(Booking.starts_at.desc())
    if not include_cancelled:
        stmt = stmt.where(Booking.status != BookingStatus.cancelled)
    if q and q.strip():
        stmt = stmt.where(
            _matches(q, Contact.name, Contact.email, Company.name, Booking.topic, Consultant.name)
        )
    rows = (await session.execute(stmt)).all()
    return [
        BookingRow(
            id=b.id,
            starts_at=b.starts_at,
            ends_at=b.ends_at,
            status=b.status,
            topic=b.topic,
            consultant=cons,
            contact_name=n,
            contact_email=e,
            company=co,
            contact_timezone=tz,
            conversation_id=b.conversation_id,
            calendar_event_id=b.calendar_event_id,
        )
        for b, cons, n, e, co, tz in rows
    ]


# ---------- Tickets ----------


class TicketRow(BaseModel):
    id: uuid.UUID
    ref: str
    summary: str
    status: str
    priority: str
    company: str
    contact: str | None
    system: str
    external_id: str | None
    updates: int
    created_at: datetime
    updated_at: datetime
    # Status checked with the ticketing system on this page load: True = live,
    # False = couldn't reach it (last known status shown), None = local ticket.
    synced: bool | None = None


SYNC_TIMEOUT_S = 4.0
SYNC_CONCURRENCY = 5


async def _sync_statuses(adapter, tickets: list[Ticket]) -> dict[uuid.UUID, bool]:
    """Read each external ticket's current status (e.g. an engineer closed it in Jira).
    Read-only lookups, run in parallel with a timeout, so a slow Jira can't hang the page."""
    gate = asyncio.Semaphore(SYNC_CONCURRENCY)

    async def one(t: Ticket) -> tuple[uuid.UUID, bool]:
        async with gate:
            try:
                status_ = await asyncio.wait_for(adapter.status(t.external_id), SYNC_TIMEOUT_S)
            except Exception:  # unreachable, timed out, auth: keep the last known status
                log.warning("tickets.sync_failed", ticket=t.ref)
                return t.id, False
            if status_ and status_ != t.status:
                t.status = status_
            return t.id, True

    pending = [t for t in tickets if t.external_id and t.system != "local"]
    return dict(await asyncio.gather(*(one(t) for t in pending)))


@router.get("/tickets", response_model=list[TicketRow])
async def tickets(
    request: Request,
    open_only: bool = True,
    limit: int = Query(100, le=500),
    user: StaffUser = Depends(staff_any),
    session: AsyncSession = Depends(get_session),
):
    prio = case({"urgent": 0, "high": 1, "normal": 2, "low": 3}, value=Ticket.priority, else_=4)
    stmt = (
        select(Ticket, Company.name, Contact.name)
        .join(Company, Company.id == Ticket.company_id)
        .outerjoin(Contact, Contact.id == Ticket.contact_id)
        .order_by(prio, Ticket.updated_at.desc())
        .limit(limit)
    )
    if open_only:
        stmt = stmt.where(Ticket.status.not_in(CLOSED_TICKET))
    rows = (await session.execute(stmt)).all()
    synced = await _sync_statuses(request.app.state.tickets, [t for t, _, _ in rows])
    if open_only:  # closed in Jira since the last look: no longer open
        rows = [r for r in rows if r[0].status not in CLOSED_TICKET]
    out = [
        TicketRow(
            id=t.id,
            ref=t.ref,
            summary=t.summary,
            status=t.status,
            priority=t.priority,
            company=co,
            contact=cn,
            system=t.system,
            external_id=t.external_id,
            updates=len(t.updates or []),
            created_at=t.created_at,
            updated_at=t.updated_at,
            synced=synced.get(t.id),
        )
        for t, co, cn in rows
    ]
    # Saved after building the response (committing expires the loaded rows), so the
    # chat's ticket answers and the Overview count use the fresh statuses too.
    await session.commit()
    return out


# ---------- Settings catalogue ----------


class ConfigEntry(BaseModel):
    key: str
    description: str
    value: Any
    source: str  # published | default
    published_version: int | None
    published_at: datetime | None
    draft_versions: int


@router.get("/config", response_model=list[ConfigEntry])
async def config_catalogue(
    user: StaffUser = Depends(managers), session: AsyncSession = Depends(get_session)
):
    published = {
        r.key: r
        for r in (
            await session.execute(
                select(ConfigVersion).where(ConfigVersion.status == ConfigStatus.published)
            )
        ).scalars()
    }
    drafts = dict(
        (
            await session.execute(
                select(ConfigVersion.key, func.count())
                .where(
                    and_(
                        ConfigVersion.status == ConfigStatus.draft,
                        ConfigVersion.key.in_(EDITABLE_CONFIG),
                    )
                )
                .group_by(ConfigVersion.key)
            )
        ).all()
    )
    out = []
    for key, desc in EDITABLE_CONFIG.items():
        p = published.get(key)
        out.append(
            ConfigEntry(
                key=key,
                description=desc,
                value=p.value if p else config_store.DEFAULTS.get(key),
                source="published" if p else "default",
                published_version=p.version if p else None,
                published_at=p.published_at if p else None,
                draft_versions=drafts.get(key, 0),
            )
        )
    return out


# ---------- Public content (FAQ and service pages) ----------


class ContentPage(BaseModel):
    slug: str
    key: str  # config key for drafts, versions and publishing
    title: str
    doc_type: str
    raw: str
    origin: str  # file | dashboard | draft (new page, not yet published)
    published_version: int | None
    published_at: datetime | None
    draft_versions: int
    sections: int
    indexed_chunks: int


@router.get("/content", response_model=list[ContentPage])
async def content_pages(
    user: StaffUser = Depends(managers), session: AsyncSession = Depends(get_session)
):
    pages = await public_kb.page_sources(session, Path(get_settings().kb_content_dir))
    is_content = ConfigVersion.key.startswith(public_kb.CONTENT_PREFIX)
    versions = {
        r.key: r
        for r in (
            await session.execute(
                select(ConfigVersion).where(
                    is_content, ConfigVersion.status == ConfigStatus.published
                )
            )
        ).scalars()
    }
    drafts = dict(
        (
            await session.execute(
                select(ConfigVersion.key, func.count())
                .where(is_content, ConfigVersion.status == ConfigStatus.draft)
                .group_by(ConfigVersion.key)
            )
        ).all()
    )
    chunks = dict(
        (
            await session.execute(
                select(Document.source_uri, func.count(PublicChunk.id))
                .join(PublicChunk, PublicChunk.document_id == Document.id)
                .where(Document.corpus == Corpus.public)
                .group_by(Document.source_uri)
            )
        ).all()
    )
    out = []
    for uri, (raw, origin) in sorted(pages.items()):
        slug = public_kb.slug_for(uri)
        key = public_kb.CONTENT_PREFIX + slug
        doc = public_kb.parse_markdown(uri, raw)
        v = versions.get(key)
        out.append(
            ContentPage(
                slug=slug,
                key=key,
                title=doc.title,
                doc_type=doc.doc_type,
                raw=raw,
                origin=origin,
                published_version=v.version if v else None,
                published_at=v.published_at if v else None,
                draft_versions=drafts.get(key, 0),
                sections=len({s for s, _ in doc.chunks}),
                indexed_chunks=chunks.get(uri, 0),
            )
        )
    # New pages that so far exist only as drafts.
    for key in sorted(set(drafts) - {p.key for p in out}):
        slug = public_kb.slug_for(key)
        if public_kb.SLUG.fullmatch(slug):
            out.append(
                ContentPage(
                    slug=slug,
                    key=key,
                    title=slug.replace("-", " ").title(),
                    doc_type="page",
                    raw="",
                    origin="draft",
                    published_version=None,
                    published_at=None,
                    draft_versions=drafts[key],
                    sections=0,
                    indexed_chunks=0,
                )
            )
    return out


class ContentPreviewIn(BaseModel):
    raw: str = Field(max_length=50_000)


class ContentPreview(BaseModel):
    title: str
    doc_type: str
    sections: list[dict[str, Any]]  # [{section, chars}]
    issues: list[str]  # why it can't be published (empty = fine)


@router.post("/content/preview", response_model=ContentPreview)
async def content_preview(body: ContentPreviewIn, user: StaffUser = Depends(managers)):
    doc = public_kb.parse_markdown("content/public/preview.md", body.raw)
    return ContentPreview(
        title=doc.title,
        doc_type=doc.doc_type,
        sections=[{"section": s or "Introduction", "chars": len(t)} for s, t in doc.chunks],
        issues=public_kb.content_issues(body.raw),
    )


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=300)


class AskHit(BaseModel):
    title: str
    section: str | None
    score: float
    text: str


@router.post("/content/ask", response_model=list[AskHit])
async def content_ask(
    body: AskIn,
    request: Request,
    user: StaffUser = Depends(managers),
    session: AsyncSession = Depends(get_session),
):
    """What the website assistant would find for this question in the live content."""
    hits = await public_kb.search(session, request.app.state.embedder, body.question)
    return [AskHit(title=h.title, section=h.section, score=h.score, text=h.content) for h in hits]


# ---------- Unanswered topics ----------


class UnansweredTopic(BaseModel):
    topic: str  # the most recent question in the group
    count: int
    examples: list[str]
    last_asked: datetime
    conversation_ids: list[uuid.UUID]


TOPIC_SIMILARITY = 0.8  # questions this close (cosine) count as the same topic


@router.get("/unanswered", response_model=list[UnansweredTopic])
async def unanswered(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    user: StaffUser = Depends(managers),
    session: AsyncSession = Depends(get_session),
):
    """Visitor questions the approved content had nothing for, grouped by topic.

    Signal: search_knowledge ran and returned no results, so the assistant said it didn't
    know and offered a call. These are the gaps in the public content."""
    since = datetime.now(UTC) - timedelta(days=days)
    asked = aliased(Message)
    question = (
        select(asked.content)
        .where(
            asked.conversation_id == Message.conversation_id,
            asked.role == MessageRole.user,
            asked.id < Message.id,
        )
        .order_by(asked.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Message.conversation_id, Message.created_at, question)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.role == MessageRole.tool,
                Message.tool_name == "search_knowledge",
                Message.content.startswith(NO_RESULTS),
                Message.created_at >= since,
                Conversation.channel != "internal",
            )
            .order_by(Message.created_at.desc())
            .limit(500)
        )
    ).all()
    rows = [r for r in rows if r[2]]
    if not rows:
        return []
    vectors = await request.app.state.embedder.embed_documents([r[2] for r in rows])
    groups: list[dict[str, Any]] = []
    for (conv_id, at, text), vec in zip(rows, vectors, strict=True):
        for g in groups:  # vectors are normalised, so the dot product is the cosine
            if sum(a * b for a, b in zip(g["vec"], vec, strict=True)) >= TOPIC_SIMILARITY:
                break
        else:
            g = {"vec": vec, "items": []}
            groups.append(g)
        g["items"].append((conv_id, at, text.strip()))
    out = []
    for g in groups:
        items = g["items"]  # newest first
        out.append(
            UnansweredTopic(
                topic=items[0][2][:200],
                count=len(items),
                examples=list(dict.fromkeys(t[:200] for _, _, t in items))[:3],
                last_asked=items[0][1],
                conversation_ids=list(dict.fromkeys(c for c, _, _ in items))[:5],
            )
        )
    out.sort(key=lambda t: (-t.count, -t.last_asked.timestamp()))
    return out[:50]


# ---------- Client access (internal knowledge assistant) ----------

admins = require_roles("admin")


class CompanyOut(BaseModel):
    id: uuid.UUID
    name: str
    domain: str | None


class GrantOut(BaseModel):
    id: int
    staff_email: str
    company_id: uuid.UUID
    company: str
    granted_by: str | None
    created_at: datetime


class ClientAccessOut(BaseModel):
    grants: list[GrantOut]
    companies: list[CompanyOut]


@router.get("/client-access", response_model=ClientAccessOut)
async def client_access(
    user: StaffUser = Depends(admins), session: AsyncSession = Depends(get_session)
):
    rows = (
        await session.execute(
            select(ClientAccess, Company.name)
            .join(Company, Company.id == ClientAccess.company_id)
            .order_by(ClientAccess.staff_email, Company.name)
        )
    ).all()
    companies = (await session.execute(select(Company).order_by(Company.name))).scalars().all()
    return ClientAccessOut(
        grants=[
            GrantOut(
                id=g.id,
                staff_email=g.staff_email,
                company_id=g.company_id,
                company=name,
                granted_by=g.granted_by,
                created_at=g.created_at,
            )
            for g, name in rows
        ],
        companies=[CompanyOut(id=c.id, name=c.name, domain=c.domain) for c in companies],
    )


class GrantIn(BaseModel):
    # Plain format check: staff logins may use reserved domains such as example.test.
    staff_email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    company_id: uuid.UUID


@router.post("/client-access", response_model=GrantOut, status_code=201)
async def grant_client_access(
    body: GrantIn, user: StaffUser = Depends(admins), session: AsyncSession = Depends(get_session)
):
    company = await session.get(Company, body.company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    email = body.staff_email.strip().lower()  # access_for() matches on the lowercased email
    existing = (
        await session.execute(
            select(ClientAccess.id).where(
                ClientAccess.staff_email == email, ClientAccess.company_id == company.id
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{email} already has {company.name}")
    grant = ClientAccess(
        staff_email=email, company_id=company.id, granted_by=user.username or user.sub
    )
    session.add(grant)
    await session.flush()
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="client_access.grant",
        target_type="company",
        target_id=str(company.id),
        staff_email=email,
    )
    await session.commit()
    await session.refresh(grant)
    return GrantOut(
        id=grant.id,
        staff_email=grant.staff_email,
        company_id=company.id,
        company=company.name,
        granted_by=grant.granted_by,
        created_at=grant.created_at,
    )


@router.delete("/client-access/{grant_id}", status_code=204)
async def revoke_client_access(
    grant_id: int, user: StaffUser = Depends(admins), session: AsyncSession = Depends(get_session)
):
    grant = await session.get(ClientAccess, grant_id)
    if grant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "grant not found")
    audit.record(
        session,
        actor_type="staff",
        actor_id=user.sub,
        action="client_access.revoke",
        target_type="company",
        target_id=str(grant.company_id),
        staff_email=grant.staff_email,
    )
    await session.delete(grant)
    await session.commit()


# ---------- Rule editors: options and previews ----------


@router.get("/rules/options")
async def rule_options(user: StaffUser = Depends(managers)) -> dict[str, Any]:
    """The values the rule forms offer (the same lists the assistant uses)."""
    return {
        "needs": rules_schema.NEEDS,
        "sizes": rules_schema.SIZES,
        "budgets": rules_schema.BUDGETS,
        "timelines": rules_schema.TIMELINES,
        "roles": rules_schema.ROLES,
        "requirable": rules_schema.REQUIRABLE,
        "urgencies": rules_schema.URGENCIES,
        "handover_reasons": rules_schema.HANDOVER_REASONS,
    }


class RulePreviewIn(BaseModel):
    value: dict[str, Any]
    sample: dict[str, Any] = {}


@router.post("/rules/{key}/preview")
async def rule_preview(
    key: str, body: RulePreviewIn, user: StaffUser = Depends(managers)
) -> dict[str, Any]:
    """What the assistant would do with these (unsaved) rules. Changes nothing."""
    if key not in rules_schema.SCHEMAS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no preview for this setting")
    try:
        value = rules_schema.validate(key, body.value)
        return {"ok": True, "result": rules_schema.preview(key, value, body.sample)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
