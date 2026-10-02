"""Async run processor + lease janitor.

Each tick the poller (1) **sweeps** lapsed/wedged target leases so a crashed or
overrunning run frees its lock (ADR-0011 §5), (2) **prunes** retained check-result
history past the rolling window while preserving each series' last-green anchor
(ADR-0015) — at most once per ``RETENTION_SWEEP_INTERVAL_SECONDS``, (3) reaps
wedged runs, (4) hands new **PENDING** requests to the dispatcher, and (5)
**drains QUEUED** requests (FIFO by age) so a run that was waiting on a busy
target picks up the freed lease. Requests run on the engine's dispatcher pool,
so a long Run never stalls the tick; ``run_service`` owns the per-target locking
and fan-out concurrency.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Optional

from sqlmodel import select

from app.core.enums import RunStatus
from app.models import RunRequest
from app.services import locking, retention, run_service

logger = logging.getLogger(__name__)

_WORKER_ID = f"{socket.gethostname()}-poller"
_last_history_sweep: Optional[float] = None


def _settings():
    from app.core.config import settings

    return settings


def _sweep_leases() -> None:
    from app.db.session import session_scope

    with session_scope() as session:
        locking.sweep(session)


def _sweep_history() -> None:
    from app.db.session import session_scope

    with session_scope() as session:
        retention.sweep(session)


def _sweep_history_if_due(now: Optional[float] = None) -> bool:
    global _last_history_sweep
    now = time.monotonic() if now is None else now
    if (_last_history_sweep is not None
            and now - _last_history_sweep < _settings().RETENTION_SWEEP_INTERVAL_SECONDS):
        return False
    _last_history_sweep = now
    _sweep_history()
    return True


def _pending_ids(limit: int) -> list[str]:
    from app.db.session import session_scope

    with session_scope() as session:
        return [
            r.id
            for r in session.exec(
                select(RunRequest)
                .where(RunRequest.status == RunStatus.PENDING)
                .order_by(RunRequest.created_at)  # type: ignore[arg-type]
                .limit(limit)
            ).all()
        ]


def tick() -> None:
    """One poller pass. Returns once the requests are handed off, not when they finish."""
    settings = _settings()
    _sweep_leases()
    _sweep_history_if_due()
    run_service.reap_wedged_runs()
    for request_id in _pending_ids(settings.POLLER_BATCH_SIZE):
        run_service.process_request(request_id, _WORKER_ID)
    run_service.drain_queued(_WORKER_ID, settings.POLLER_BATCH_SIZE)


async def start_poller() -> None:
    logger.info("Poller started (interval=%ss)", _settings().POLLER_INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            logger.error("Poller iteration failed: %s", exc, exc_info=True)
        await asyncio.sleep(_settings().POLLER_INTERVAL_SECONDS)
