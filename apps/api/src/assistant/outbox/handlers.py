"""Effect handlers run by the outbox worker. Each returns an external reference.

Handlers receive the outbox idempotency key and pass it downstream where the target
supports de-duplication (e.g. the email Message-ID), shrinking the at-least-once window.
"""

import asyncio
import smtplib
from collections.abc import Awaitable, Callable
from email.message import EmailMessage
from typing import Any

import httpx
import structlog

from assistant.settings import get_settings

log = structlog.get_logger()

Handler = Callable[[dict[str, Any], str], Awaitable[str | None]]


class PermanentError(Exception):
    """Retrying won't help (bad payload, rejected recipient): dead-letter immediately."""


def _send_smtp(msg: EmailMessage) -> None:
    s = get_settings()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
        smtp.send_message(msg)


async def send_email(payload: dict[str, Any], key: str) -> str:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = payload["to"]
    msg["Subject"] = payload["subject"]
    msg["Message-ID"] = f"<{key}@assistant.local>"
    msg.set_content(payload["body"])
    try:
        await asyncio.to_thread(_send_smtp, msg)
    except smtplib.SMTPRecipientsRefused as e:
        raise PermanentError(f"recipient refused: {e}") from e
    return msg["Message-ID"]


async def slack_notify(payload: dict[str, Any], key: str) -> str:
    url = get_settings().slack_notify_webhook_url
    if not url:
        log.info("slack.disabled", text=payload.get("text"))
        return "slack-disabled"
    prefix = {"high": ":red_circle: ", "normal": "", "low": ""}.get(payload.get("urgency", ""), "")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(url, json={"text": prefix + payload["text"]})
    if 400 <= r.status_code < 500 and r.status_code != 429:
        raise PermanentError(f"slack rejected: {r.status_code} {r.text[:200]}")
    r.raise_for_status()
    return f"slack:{key[:12]}"


def default_handlers() -> dict[str, Handler]:
    return {"email.send": send_email, "slack.notify": slack_notify}
