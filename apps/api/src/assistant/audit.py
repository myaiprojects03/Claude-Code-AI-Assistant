from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from assistant.models import AuditLog


def record(
    session: AsyncSession,
    *,
    actor_type: str,
    actor_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    **details: Any,
) -> None:
    """Add an audit row to the current transaction (caller commits)."""
    from assistant.auth import current_staff

    user = current_staff.get()
    if actor_type == "staff" and user is not None and user.sub == actor_id:
        details.setdefault("actor_name", user.display_name)
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
    )
