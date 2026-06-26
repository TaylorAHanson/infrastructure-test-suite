"""Async run processor.

The poller claims PENDING ``run_request`` rows (with a simple lock), expands and
executes them via :mod:`app.services.run_service`, and writes results back. This
is the async/polling pattern the web process uses so request submission never
blocks on heavy validation.

For reliable *scheduled* execution at scale, the Databricks Job owns timing and
heavy fan-out; the poller is the in-app dispatcher and a local-dev fallback.
"""
from __future__ import annotations

import asyncio
import logging
import socket

from sqlmodel import select

from app.core.config import settings
from app.core.enums import RunStatus
from app.core.ids import utcnow
from app.models import RunRequest
from app.services import run_service, schedule_service

logger = logging.getLogger(__name__)

_WORKER_ID = f"{socket.gethostname()}-poller"


def _claim_pending(limit: int) -> list[str]:
    from app.db.session import session_scope

    claimed: list[str] = []
    with session_scope() as session:
        rows = session.exec(
            select(RunRequest)
            .where(RunRequest.status == RunStatus.PENDING)
            .order_by(RunRequest.created_at)  # type: ignore[arg-type]
            .limit(limit)
        ).all()
        for req in rows:
            req.status = RunStatus.QUEUED
            req.locked_by = _WORKER_ID
            req.locked_at = utcnow()
            session.add(req)
            claimed.append(req.id)
        session.commit()
    return claimed


def _tick_fallback_schedules() -> None:
    """Local-dev only: create run_requests for due schedules.

    No-op in a real workspace where the Databricks Job is authoritative.
    """
    if settings.is_databricks_app:
        return
    from app.db.session import session_scope

    with session_scope() as session:
        for sched in schedule_service.due_schedules(session):
            schedule_service.fire_schedule(session, sched)
            logger.info("Fallback schedule fired: %s", sched.name)


async def start_poller() -> None:
    logger.info("Poller started (interval=%ss, batch=%s)", settings.POLLER_INTERVAL_SECONDS, settings.POLLER_BATCH_SIZE)
    while True:
        try:
            _tick_fallback_schedules()
            claimed = await asyncio.to_thread(_claim_pending, settings.POLLER_BATCH_SIZE)
            for request_id in claimed:
                await asyncio.to_thread(run_service.process_request, request_id, _WORKER_ID)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            logger.error("Poller iteration failed: %s", exc, exc_info=True)
        await asyncio.sleep(settings.POLLER_INTERVAL_SECONDS)
