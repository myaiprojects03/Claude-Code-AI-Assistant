"""Outbox worker: at-least-once delivery with backoff, dead-lettering and alerts.

Several workers can run at once: rows are claimed with FOR UPDATE SKIP LOCKED.
Rows stuck in `processing` (worker crashed mid-effect) are re-queued after a timeout.
"""

import asyncio
import random
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from prometheus_client import start_http_server
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant import alerts, metrics
from assistant.db import get_sessionmaker
from assistant.logs import configure_logging
from assistant.models import IntentStatus, OutboxItem, OutboxStatus, ToolIntent
from assistant.outbox.handlers import Handler, PermanentError, build_handlers
from assistant.settings import Settings, get_settings

log = structlog.get_logger()


def backoff_seconds(attempt: int, base: float = 5.0, cap: float = 3600.0) -> float:
    """Exponential backoff with ±20% jitter: ~5s, 10s, 20s, 40s ... capped at 1h."""
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    return delay * random.uniform(0.8, 1.2)


@dataclass
class Claimed:
    id: int
    kind: str
    payload: dict[str, Any]
    key: str
    attempts: int
    max_attempts: int
    intent_id: Any


class OutboxWorker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        handlers: dict[str, Handler] | None = None,
        settings: Settings | None = None,
    ):
        self.sm = sessionmaker or get_sessionmaker()
        self.handlers = handlers if handlers is not None else build_handlers(self.sm)
        self.s = settings or get_settings()

    async def reclaim_stuck(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.s.outbox_stuck_after_s)
        async with self.sm() as s, s.begin():
            res = await s.execute(
                update(OutboxItem)
                .where(OutboxItem.status == OutboxStatus.processing, OutboxItem.locked_at < cutoff)
                .values(status=OutboxStatus.pending, locked_at=None)
            )
        if res.rowcount:
            log.warning("outbox.reclaimed_stuck", count=res.rowcount)
        return res.rowcount or 0

    async def claim(self) -> list[Claimed]:
        async with self.sm() as s, s.begin():
            rows = (
                (
                    await s.execute(
                        select(OutboxItem)
                        .where(
                            OutboxItem.status == OutboxStatus.pending,
                            OutboxItem.next_attempt_at <= func.now(),
                        )
                        .order_by(OutboxItem.id)
                        .limit(self.s.outbox_batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            out = []
            for r in rows:
                r.status = OutboxStatus.processing
                r.locked_at = func.now()
                r.attempts += 1
                out.append(
                    Claimed(
                        r.id,
                        r.kind,
                        dict(r.payload),
                        r.idempotency_key,
                        r.attempts,
                        r.max_attempts,
                        r.intent_id,
                    )
                )
            return out

    async def _finish_intent(self, s: AsyncSession, intent_id: Any) -> None:
        """Mark the intent done once all its outbox items are done."""
        items = (
            await s.execute(
                select(OutboxItem.status, OutboxItem.external_ref).where(
                    OutboxItem.intent_id == intent_id
                )
            )
        ).all()
        if items and all(st == OutboxStatus.done for st, _ in items):
            await s.execute(
                update(ToolIntent)
                .where(ToolIntent.id == intent_id)
                .values(
                    status=IntentStatus.done, result={"external_refs": [ref for _, ref in items]}
                )
            )

    async def process(self, item: Claimed) -> str:
        handler = self.handlers.get(item.kind)
        error: str | None = None
        permanent = False
        ref: str | None = None
        if handler is None:
            error, permanent = f"no handler for kind '{item.kind}'", True
        else:
            try:
                ref = await handler(item.payload, item.key)
            except PermanentError as e:
                error, permanent = str(e), True
            except Exception as e:
                error = f"{type(e).__name__}: {e}"

        async with self.sm() as s, s.begin():
            if error is None:
                await s.execute(
                    update(OutboxItem)
                    .where(OutboxItem.id == item.id)
                    .values(
                        status=OutboxStatus.done, external_ref=ref, last_error=None, locked_at=None
                    )
                )
                if item.intent_id:
                    await self._finish_intent(s, item.intent_id)
                outcome = "done"
            elif permanent or item.attempts >= item.max_attempts:
                await s.execute(
                    update(OutboxItem)
                    .where(OutboxItem.id == item.id)
                    .values(status=OutboxStatus.dead, last_error=error, locked_at=None)
                )
                if item.intent_id:
                    await s.execute(
                        update(ToolIntent)
                        .where(ToolIntent.id == item.intent_id)
                        .values(status=IntentStatus.failed, error=error)
                    )
                outcome = "dead"
            else:
                delay = backoff_seconds(item.attempts)
                await s.execute(
                    update(OutboxItem)
                    .where(OutboxItem.id == item.id)
                    .values(
                        status=OutboxStatus.pending,
                        last_error=error,
                        locked_at=None,
                        next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay),
                    )
                )
                outcome = "retry"

        metrics.OUTBOX_PROCESSED.labels(kind=item.kind, outcome=outcome).inc()
        if outcome == "dead":
            await alerts.send_alert(
                "critical",
                f"Outbox item dead-lettered: {item.kind}",
                outbox_id=item.id,
                attempts=item.attempts,
                error=error,
            )
        elif outcome == "retry":
            log.warning(
                "outbox.retry", id=item.id, kind=item.kind, attempt=item.attempts, error=error
            )
        return outcome

    async def update_gauges(self) -> None:
        async with self.sm() as s:
            counts = dict(
                (
                    await s.execute(
                        select(OutboxItem.status, func.count()).group_by(OutboxItem.status)
                    )
                ).all()
            )
        metrics.OUTBOX_PENDING.set(counts.get(OutboxStatus.pending, 0))
        metrics.OUTBOX_DEAD.set(counts.get(OutboxStatus.dead, 0))

    async def run_retention(self) -> dict[str, int]:
        """GDPR retention: anonymise or delete data past its period (privacy.py)."""
        from assistant import audit, privacy
        from assistant.api.routes.privacy import retention_policy

        async with self.sm() as s:
            stats = await privacy.apply_retention(s, retention_policy())
            if any(stats.values()):
                audit.record(
                    s,
                    actor_type="system",
                    actor_id="retention",
                    action="privacy.retention",
                    **stats,
                )
            await s.commit()
        log.info("privacy.retention", **stats)
        return stats

    async def run_once(self) -> int:
        await self.reclaim_stuck()
        batch = await self.claim()
        if batch:
            await asyncio.gather(*(self.process(i) for i in batch))
        return len(batch)

    async def run_forever(self, stop: asyncio.Event) -> None:
        log.info("outbox.worker_started", handlers=sorted(self.handlers))
        gauge_tick = 0
        next_retention = 0.0
        while not stop.is_set():
            try:
                n = await self.run_once()
                gauge_tick += 1
                if gauge_tick % 15 == 0:
                    await self.update_gauges()
                if time.monotonic() >= next_retention:
                    await self.run_retention()
                    next_retention = time.monotonic() + self.s.retention_interval_s
            except Exception as e:
                log.exception("outbox.loop_error")
                await alerts.send_alert("critical", "Outbox worker loop error", error=str(e))
                n = 0
            if n == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.s.outbox_poll_interval_s)
                except TimeoutError:
                    pass


def main() -> None:
    configure_logging()
    s = get_settings()
    start_http_server(s.worker_metrics_port)

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                signal.signal(sig, lambda *_: stop.set())
        await OutboxWorker().run_forever(stop)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
