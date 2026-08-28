"""Async run processor.

Claims PENDING ``run_request`` rows (a simple lock), then expands + executes
them via :mod:`app.services.run_service`. This is the in-app dispatcher so
request submission never blocks on heavy validation.
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
from app.services import run_service

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


async def start_poller() -> None:
    logger.info("Poller started (interval=%ss)", settings.POLLER_INTERVAL_SECONDS)
    while True:
        try:
            claimed = await asyncio.to_thread(_claim_pending, settings.POLLER_BATCH_SIZE)
            for request_id in claimed:
                await asyncio.to_thread(run_service.process_request, request_id, _WORKER_ID)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            logger.error("Poller iteration failed: %s", exc, exc_info=True)
        await asyncio.sleep(settings.POLLER_INTERVAL_SECONDS)
