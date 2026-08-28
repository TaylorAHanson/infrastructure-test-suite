"""Async run processor + lease janitor.

Each tick the poller (1) **sweeps** lapsed/wedged target leases so a crashed or
overrunning run frees its lock (ADR-0011 §5), (2) **prunes** retained check-result
history past the rolling window while preserving each series' last-green anchor
(ADR-0015), (3) dispatches new **PENDING** requests, and (4) **drains QUEUED**
requests (FIFO by age) so a run that was waiting on a busy target picks up the
freed lease. This is the in-app dispatcher so request submission never blocks on
heavy validation; ``run_service`` owns the per-target locking and fan-out
concurrency.
"""
from __future__ import annotations

import asyncio
import logging
import socket

from sqlmodel import select

from app.core.config import settings
from app.core.enums import RunStatus
from app.models import RunRequest
from app.services import locking, retention, run_service

logger = logging.getLogger(__name__)

_WORKER_ID = f"{socket.gethostname()}-poller"


def _sweep_leases() -> None:
    from app.db.session import session_scope

    with session_scope() as session:
        locking.sweep(session)


def _sweep_history() -> None:
    from app.db.session import session_scope

    with session_scope() as session:
        retention.sweep(session)


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


async def start_poller() -> None:
    logger.info("Poller started (interval=%ss)", settings.POLLER_INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.to_thread(_sweep_leases)
            await asyncio.to_thread(_sweep_history)
            await asyncio.to_thread(run_service.reap_wedged_runs)
            for request_id in await asyncio.to_thread(_pending_ids, settings.POLLER_BATCH_SIZE):
                await asyncio.to_thread(run_service.process_request, request_id, _WORKER_ID)
            await asyncio.to_thread(run_service.drain_queued, _WORKER_ID, settings.POLLER_BATCH_SIZE)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            logger.error("Poller iteration failed: %s", exc, exc_info=True)
        await asyncio.sleep(settings.POLLER_INTERVAL_SECONDS)
