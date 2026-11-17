"""Existing-client verification: email match + one-time code.

Privacy rules:
* The reply is identical whether or not the email is known, so the chat can't be used to
  discover who our clients are. A code is only emailed when the address is on record.
* Only an HMAC of the code is stored. Codes expire after 10 minutes, allow 3 attempts,
  and at most 3 codes can be requested per conversation per hour.
"""

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant import audit
from assistant.models import Company, Contact, Conversation, VerificationCode
from assistant.settings import get_settings
from assistant.tools.registry import ToolRejected, enqueue

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 3
MAX_CODES_PER_HOUR = 3
GENERIC_SENT = (
    "If that email address is registered with us, a 6-digit code has just been "
    "sent to it. Ask the visitor to type the code here."
)


def _hash(email: str, code: str) -> str:
    key = get_settings().app_secret.encode()
    return hmac.new(key, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


async def send_code(session: AsyncSession, conversation_id: uuid.UUID, email: str) -> str:
    email = email.strip().lower()
    since = datetime.now(UTC) - timedelta(hours=1)
    recent = (
        await session.execute(
            select(func.count())
            .select_from(VerificationCode)
            .where(
                VerificationCode.conversation_id == conversation_id,
                VerificationCode.created_at > since,
            )
        )
    ).scalar_one()
    if recent >= MAX_CODES_PER_HOUR:
        raise ToolRejected(
            "Too many codes requested. Ask them to try again later, or offer "
            "to pass their request to a consultant."
        )

    contact = (
        await session.execute(select(Contact).where(Contact.email == email))
    ).scalar_one_or_none()
    code = f"{secrets.randbelow(10**6):06d}"
    row = VerificationCode(
        conversation_id=conversation_id,
        email=email,
        contact_id=contact.id if contact else None,
        code_hash=_hash(email, code),
        expires_at=datetime.now(UTC) + CODE_TTL,
    )
    session.add(row)
    await session.flush()
    if contact is not None:
        await enqueue(
            session,
            "email.send",
            {
                "to": email,
                "subject": "Your verification code",
                "body": (
                    f"Your verification code is {code}.\n\nIt expires in 10 minutes. If you "
                    "didn't ask for this, you can ignore this email."
                ),
            },
            key=f"otp:{row.id}",
        )
    audit.record(
        session,
        actor_type="contact",
        actor_id=email,
        action="verification.requested",
        target_type="conversation",
        target_id=str(conversation_id),
        known=contact is not None,
    )
    return GENERIC_SENT


async def check_code(session: AsyncSession, conversation_id: uuid.UUID, code: str) -> Contact:
    row = (
        await session.execute(
            select(VerificationCode)
            .where(
                VerificationCode.conversation_id == conversation_id,
                VerificationCode.used_at.is_(None),
                VerificationCode.expires_at > datetime.now(UTC),
            )
            .order_by(VerificationCode.id.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.attempts >= MAX_ATTEMPTS:
        raise ToolRejected("No active code. Offer to send a new one.")
    row.attempts += 1
    ok = hmac.compare_digest(row.code_hash, _hash(row.email, code.strip()))
    if not ok or row.contact_id is None:
        left = MAX_ATTEMPTS - row.attempts
        await session.flush()
        raise ToolRejected(
            "That code isn't right."
            + (
                f" {left} attempt(s) left."
                if left
                else " No attempts left: offer to send a new code."
            )
        )

    row.used_at = datetime.now(UTC)
    conv = await session.get(Conversation, conversation_id)
    contact = await session.get(Contact, row.contact_id)
    conv.verified = True
    conv.contact_id = contact.id
    conv.state = {
        **(conv.state or {}),
        "verified_email": row.email,
        "verified_at": row.used_at.isoformat(),
    }
    audit.record(
        session,
        actor_type="contact",
        actor_id=row.email,
        action="verification.passed",
        target_type="conversation",
        target_id=str(conversation_id),
    )
    await session.flush()
    return contact


@dataclass
class VerifiedClient:
    contact: Contact
    company: Company | None


async def require_verified(
    session: AsyncSession, conversation_id: uuid.UUID, *, client: bool = False
) -> VerifiedClient:
    """The verified contact for this conversation, or a ToolRejected the model can act on."""
    conv = await session.get(Conversation, conversation_id)
    if not conv.verified or conv.contact_id is None:
        raise ToolRejected(
            "Not verified yet. Ask for the email they use with us, call "
            "send_verification_code, then verify_code with the 6-digit code."
        )
    contact = await session.get(Contact, conv.contact_id)
    company = await session.get(Company, contact.company_id) if contact.company_id else None
    if client and (not contact.is_client or company is None):
        raise ToolRejected(
            "We don't have client records for this person, so there are no "
            "tickets or projects to show. Offer help or a consultant call."
        )
    return VerifiedClient(contact, company)


async def latest_visitor_message(session: AsyncSession, conversation_id: uuid.UUID) -> str | None:
    """Text of the visitor's most recent message in this conversation."""
    from assistant.models import Message, MessageRole

    return (
        await session.execute(
            select(Message.content)
            .where(Message.conversation_id == conversation_id, Message.role == MessageRole.user)
            .order_by(Message.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


async def emails_typed_by_visitor(
    session: AsyncSession, conversation_id: uuid.UUID, last_n: int = 3
) -> list[str]:
    """Distinct email addresses in the visitor's last few messages, most recent first."""
    from assistant.models import Message, MessageRole

    rows = (
        await session.execute(
            select(Message.content)
            .where(Message.conversation_id == conversation_id, Message.role == MessageRole.user)
            .order_by(Message.id.desc())
            .limit(last_n)
        )
    ).scalars()
    seen: list[str] = []
    for text in rows:
        for e in _EMAIL.findall(text or ""):
            e = e.lower().rstrip(".")
            if e not in seen:
                seen.append(e)
    return seen
