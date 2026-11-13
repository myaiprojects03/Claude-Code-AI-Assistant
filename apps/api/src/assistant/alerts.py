"""Failure alerts to Slack (free incoming webhook). Falls back to an error log line.

De-duplicated per title for a cool-down window so a failing dependency can't flood the channel.
"""

import time
from typing import Any, Literal

import httpx
import structlog

from assistant import metrics
from assistant.settings import get_settings

log = structlog.get_logger()
Severity = Literal["info", "warning", "critical"]

_COOLDOWN_S = 300
_last_sent: dict[str, float] = {}
_ICON = {"info": ":information_source:", "warning": ":warning:", "critical": ":rotating_light:"}


def _should_send(title: str) -> bool:
    now = time.monotonic()
    if now - _last_sent.get(title, -_COOLDOWN_S) < _COOLDOWN_S:
        return False
    _last_sent[title] = now
    return True


async def send_alert(severity: Severity, title: str, **details: Any) -> bool:
    log.error("alert", severity=severity, title=title, **details)
    if not _should_send(title):
        return False
    url = get_settings().slack_alert_webhook_url
    if not url:
        metrics.ALERTS_SENT.labels(severity=severity, delivered="false").inc()
        return False
    lines = [f"{_ICON[severity]} *{title}*"] + [f"• {k}: `{v}`" for k, v in details.items()]
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(url, json={"text": "\n".join(lines)})
            r.raise_for_status()
    except httpx.HTTPError as e:
        log.error("alert.delivery_failed", error=str(e))
        metrics.ALERTS_SENT.labels(severity=severity, delivered="false").inc()
        return False
    metrics.ALERTS_SENT.labels(severity=severity, delivered="true").inc()
    return True


def _reset_for_tests() -> None:
    _last_sent.clear()
