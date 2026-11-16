"""Contacts and leads: our own records, always written first. CRM sync happens via the outbox."""

import hashlib
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant import config_store
from assistant.models import Booking, BookingStatus, Company, Contact, Conversation, Lead
from assistant.qualification import PROFILE_FIELDS, assess
from assistant.tools.registry import canonical_json, enqueue


async def upsert_contact(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    email: str | None = None,
    name: str | None = None,
    phone: str | None = None,
    company: str | None = None,
) -> Contact | None:
    """Find or create the contact for this conversation and link it. Email is the identity."""
    conv = await session.get(Conversation, conversation_id)
    assert conv is not None
    contact = await session.get(Contact, conv.contact_id) if conv.contact_id else None
    if email:
        email = email.strip().lower()
        by_email = (
            await session.execute(select(Contact).where(Contact.email == email))
        ).scalar_one_or_none()
        if by_email is not None:
            contact = by_email
        elif contact is None or (contact.email and contact.email != email):
            contact = Contact(id=uuid.uuid4(), email=email)
            session.add(contact)
        else:
            contact.email = email
    if contact is None:
        if not (name or phone or company):
            return None
        contact = Contact(id=uuid.uuid4())
        session.add(contact)

    if name:
        contact.name = name.strip()
    if phone:
        contact.phone = phone.strip()
    if company:
        co = (
            await session.execute(
                select(Company).where(func.lower(Company.name) == company.lower())
            )
        ).scalar_one_or_none()
        if co is None:
            co = Company(id=uuid.uuid4(), name=company.strip())
            session.add(co)
        contact.company_id = co.id
    await session.flush()
    conv.contact_id = contact.id
    return contact


async def get_or_create_lead(session: AsyncSession, conversation_id: uuid.UUID) -> Lead:
    lead = (
        await session.execute(select(Lead).where(Lead.conversation_id == conversation_id))
    ).scalar_one_or_none()
    if lead is None:
        lead = Lead(id=uuid.uuid4(), conversation_id=conversation_id, status="new")
        session.add(lead)
        await session.flush()
    return lead


def lead_profile(lead: Lead) -> dict[str, Any]:
    return {f: getattr(lead, f) for f in PROFILE_FIELDS}


async def update_lead(session: AsyncSession, lead: Lead, fields: dict[str, Any]) -> list[str]:
    """Merge non-empty fields, re-assess status, refresh summary. Returns missing fields."""
    for k, v in fields.items():
        if k in PROFILE_FIELDS and v not in (None, "", []):
            setattr(lead, k, v)
    rules = await config_store.get_value(session, "qualification.rules")
    result = assess(lead_profile(lead), rules)
    lead.status = result.status
    lead.summary = build_summary(lead, result.reasons)
    await session.flush()
    return result.missing


def build_summary(lead: Lead, reasons: list[str] | None = None) -> str:
    labels = {
        "need": "Need",
        "need_category": "Category",
        "company_size": "Company size",
        "current_systems": "Current systems",
        "budget_range": "Budget",
        "timeline": "Timeline",
        "role": "Role",
    }
    lines = [f"Lead status: {lead.status}"]
    for f, label in labels.items():
        v = getattr(lead, f)
        if v:
            lines.append(f"{label}: {', '.join(v) if isinstance(v, list) else v}")
    if reasons:
        lines.append(f"Not qualified because: {'; '.join(reasons)}")
    return "\n".join(lines)


async def queue_crm_sync(session: AsyncSession, lead: Lead, contact: Contact | None) -> bool:
    """Sync to the CRM once per distinct lead state (identical states are de-duplicated)."""
    if contact is None or not contact.email:
        return False
    lead.contact_id = contact.id
    booked = (
        await session.execute(
            select(Booking.starts_at).where(
                Booking.conversation_id == lead.conversation_id,
                Booking.status != BookingStatus.cancelled,
            )
        )
    ).scalar_one_or_none()
    state = {
        **lead_profile(lead),
        "status": lead.status,
        "email": contact.email,
        "name": contact.name,
        "phone": contact.phone,
        "booked": booked,
    }
    key = hashlib.sha256(f"crm|{lead.id}|{canonical_json(state)}".encode()).hexdigest()
    return await enqueue(session, "crm.sync", {"lead_id": str(lead.id)}, key=key)
