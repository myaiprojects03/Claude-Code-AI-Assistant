"""Core data model.

Enum-like columns are plain strings guarded by CHECK constraints, so adding a value
is a one-line migration rather than an ALTER TYPE.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSTZRANGE, UUID, ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5 via fastembed (see kb/embeddings.py)

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _check_in(column: str, enum: type[StrEnum], name: str | None = None) -> CheckConstraint:
    values = ", ".join(f"'{v.value}'" for v in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name or f"{column}_valid")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------- Enums ----------


class Channel(StrEnum):
    web = "web"
    whatsapp = "whatsapp"
    internal = "internal"


class ConversationStatus(StrEnum):
    active = "active"
    handed_over = "handed_over"
    closed = "closed"


class MessageRole(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class LeadStatus(StrEnum):
    new = "new"
    qualified = "qualified"
    unqualified = "unqualified"
    enterprise = "enterprise"


class BookingStatus(StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"


class HandoverStatus(StrEnum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"


class Urgency(StrEnum):
    low = "low"
    normal = "normal"
    high = "high"
    p1 = "p1"


class TicketPriority(StrEnum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class IntentStatus(StrEnum):
    pending = "pending"
    done = "done"
    failed = "failed"


class OutboxStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    dead = "dead"


class ConfigStatus(StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class Corpus(StrEnum):
    public = "public"
    internal = "internal"


class Sensitivity(StrEnum):
    public = "public"
    internal = "internal"
    client_confidential = "client_confidential"


# ---------- CRM-side entities ----------


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str | None] = mapped_column(String(200), unique=True)
    size_band: Mapped[str | None] = mapped_column(String(20))
    is_enterprise: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    hubspot_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Enterprise accounts: escalations go straight to this person.
    account_manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("consultants.id"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str | None] = mapped_column(String(200))
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    is_client: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    timezone: Mapped[str | None] = mapped_column(String(64))
    hubspot_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    company: Mapped[Company | None] = relationship()


class Consultant(Base):
    """Staff who receive bookings and handovers. Identity comes from Keycloak (sub)."""

    __tablename__ = "consultants"
    id: Mapped[uuid.UUID] = _uuid_pk()
    keycloak_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True)
    slack_user_id: Mapped[str | None] = mapped_column(String(32))
    calendar_id: Mapped[str | None] = mapped_column(String(320))
    skills: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = _created()


# ---------- Conversations ----------


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        _check_in("channel", Channel),
        _check_in("status", ConversationStatus),
        Index("ix_conversations_created_at", "created_at"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=ConversationStatus.active)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    staff_sub: Mapped[str | None] = mapped_column(String(64), index=True)  # internal channel
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    summary: Mapped[str | None] = mapped_column(Text)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        _check_in("role", MessageRole),
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str | None] = mapped_column(Text)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    tool_call_id: Mapped[str | None] = mapped_column(String(100))
    tool_name: Mapped[str | None] = mapped_column(String(100))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(100))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (_check_in("status", LeadStatus),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id"), unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default=LeadStatus.new)
    need: Mapped[str | None] = mapped_column(Text)
    need_category: Mapped[str | None] = mapped_column(String(80))
    company_size: Mapped[str | None] = mapped_column(String(20))
    current_systems: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    budget_range: Mapped[str | None] = mapped_column(String(20))
    timeline: Mapped[str | None] = mapped_column(String(20))
    role: Mapped[str | None] = mapped_column(String(40))
    summary: Mapped[str | None] = mapped_column(Text)
    hubspot_deal_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        _check_in("status", BookingStatus),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        # The DB itself refuses overlapping non-cancelled bookings for a consultant.
        ExcludeConstraint(
            ("consultant_id", "="),
            ("during", "&&"),
            using="gist",
            where=text("status <> 'cancelled'"),
            name="bookings_no_overlap",
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id"))
    consultant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("consultants.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    during: Mapped[Any] = mapped_column(
        TSTZRANGE, Computed("tstzrange(starts_at, ends_at, '[)')", persisted=True)
    )
    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.pending)
    topic: Mapped[str | None] = mapped_column(String(300))
    calendar_event_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Ticket(Base):
    """Support tickets. Our table is the source of truth for *who owns* a ticket (so a client
    can only ever see their own company's tickets); Jira, when connected, mirrors it."""

    __tablename__ = "tickets"
    __table_args__ = (
        _check_in("priority", TicketPriority),
        UniqueConstraint("system", "external_id", name="uq_tickets_ext"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    number: Mapped[int] = mapped_column(Integer, Identity(start=1001), unique=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id"))
    summary: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10), default=TicketPriority.normal)
    status: Mapped[str] = mapped_column(String(60), default="Open")
    system: Mapped[str] = mapped_column(String(20), default="local")  # local | jira
    external_id: Mapped[str | None] = mapped_column(String(64))
    updates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    @property
    def ref(self) -> str:
        return f"T-{self.number}"


class Project(Base):
    """Client projects, for "how's my project going?" questions (verified clients only)."""

    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(40))  # on track | at risk | delayed | complete
    summary: Mapped[str | None] = mapped_column(Text)  # client-safe latest update
    next_milestone: Mapped[str | None] = mapped_column(String(200))
    next_milestone_date: Mapped[date | None] = mapped_column(Date)
    lead_consultant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("consultants.id"))
    updated_at: Mapped[datetime] = _updated()


class VerificationCode(Base):
    """One-time codes proving a visitor controls an email address. Only a hash is stored."""

    __tablename__ = "verification_codes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"))
    email: Mapped[str] = mapped_column(String(320))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Handover(Base):
    __tablename__ = "handovers"
    __table_args__ = (_check_in("status", HandoverStatus), _check_in("urgency", Urgency))
    id: Mapped[uuid.UUID] = _uuid_pk()
    number: Mapped[int] = mapped_column(Integer, Identity(start=501), unique=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"))
    reason: Mapped[str] = mapped_column(String(80))
    urgency: Mapped[str] = mapped_column(String(10), default=Urgency.normal)
    route_to: Mapped[str] = mapped_column(String(200))  # consultant email / slack channel
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=HandoverStatus.open)
    created_at: Mapped[datetime] = _created()
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))  # staff sub
    acknowledged_by_name: Mapped[str | None] = mapped_column(String(200))
    # A named person this handover is for (e.g. an enterprise account manager), by sign-in
    # email. None = the general team queue, which any staff member may take.
    assigned_to: Mapped[str | None] = mapped_column(String(320), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_name: Mapped[str | None] = mapped_column(String(200))

    @property
    def ref(self) -> str:
        return f"H-{self.number}"


# ---------- Reliability: intents + outbox ----------


class ToolIntent(Base):
    """One row per side-effecting tool call. The idempotency key makes retries no-ops."""

    __tablename__ = "tool_intents"
    __table_args__ = (_check_in("status", IntentStatus),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100))
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default=IntentStatus.pending)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class OutboxItem(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        _check_in("status", OutboxStatus),
        Index(
            "ix_outbox_ready",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tool_intents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default=OutboxStatus.pending)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=6, server_default="6")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    external_ref: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ---------- No-code config ----------


class ConfigVersion(Base):
    """Prompts, FAQs, rules, messaging hours. One published version per key."""

    __tablename__ = "config_versions"
    __table_args__ = (
        _check_in("status", ConfigStatus),
        UniqueConstraint("key", "version", name="uq_config_key_version"),
        Index(
            "uq_config_one_published",
            "key",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer)
    value: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default=ConfigStatus.draft)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------- Knowledge (Phase 2 public, Phase 5 internal) ----------


class ClientAccess(Base):
    """Which clients a consultant may see in the internal knowledge assistant.
    Admins and managers see every client; consultants only the ones listed here."""

    __tablename__ = "client_access"
    __table_args__ = (UniqueConstraint("staff_email", "company_id", name="uq_client_access"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    staff_email: Mapped[str] = mapped_column(String(320), index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    granted_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (_check_in("corpus", Corpus), _check_in("sensitivity", Sensitivity))
    id: Mapped[uuid.UUID] = _uuid_pk()
    corpus: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    doc_type: Mapped[str | None] = mapped_column(String(60))
    source_uri: Mapped[str | None] = mapped_column(String(1000))
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"), index=True)
    sensitivity: Mapped[str] = mapped_column(String(30), default=Sensitivity.internal)
    allowed_roles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class _ChunkMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ord: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))


class PublicChunk(_ChunkMixin, Base):
    """Approved public content only. Never mixed with internal material."""

    __tablename__ = "kb_public_chunks"
    __table_args__ = (
        Index(
            "ix_kb_public_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )


class InternalChunk(_ChunkMixin, Base):
    """ACL columns are denormalised so retrieval filters before the vector search."""

    __tablename__ = "kb_internal_chunks"
    __table_args__ = (
        Index(
            "ix_kb_internal_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    sensitivity: Mapped[str] = mapped_column(String(30))
    allowed_roles: Mapped[list[str]] = mapped_column(ARRAY(String))


# ---------- Compliance ----------


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_type: Mapped[str] = mapped_column(String(20))  # staff | contact | system
    actor_id: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    __table_args__ = (Index("ix_audit_log_at", "at"),)


class DoNotContact(Base):
    __tablename__ = "do_not_contact"
    __table_args__ = (UniqueConstraint("channel", "address", name="uq_dnc_channel_address"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(20))  # email | whatsapp
    address: Mapped[str] = mapped_column(String(320))
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created()


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id"))
    kind: Mapped[str] = mapped_column(String(40))  # chat_processing | marketing
    granted: Mapped[bool] = mapped_column(Boolean)
    text_version: Mapped[str | None] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
