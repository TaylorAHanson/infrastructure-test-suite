"""The nightly regression run, started by the app's poller.

On ``NIGHTLY_CRON`` (five-field cron, UTC) the poller submits one ``full`` run of
every active Test against every Target, through the same
``run_service.submit_request`` the Run API uses, with a ``NIGHTLY`` source.

Each slot fires once: before submitting, the poller looks for a NIGHTLY request
created since the slot, under a Postgres advisory lock so two app instances
overlapping during a deploy can't both fire. A slot that passed while the app
was down is skipped rather than caught up, so a first install or a restart never
starts a surprise full run.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from croniter import croniter
from sqlmodel import select

from app.core.config import get_settings
from app.core.enums import Depth, RequestSource
from app.core.ids import utcnow
from app.models import RunRequest
from app.schemas.schemas import RunSubmit

logger = logging.getLogger(__name__)

REQUESTED_BY = "nightly"

_armed_at: Optional[datetime] = None
_handled_slot: Optional[datetime] = None
_rejected_cron: Optional[str] = None


def schedule() -> Optional[str]:
    """The cron the nightly run follows, or None when it's off."""
    global _rejected_cron
    settings = get_settings()
    cron = (settings.NIGHTLY_CRON or "").strip()
    if not cron or settings.NIGHTLY_SCHEDULE_STATUS.strip().upper() == "PAUSED":
        return None
    if len(cron.split()) != 5 or not croniter.is_valid(cron):
        if cron != _rejected_cron:
            logger.error("NIGHTLY_CRON must be a five-field cron in UTC (minute hour day month weekday), "
                         "such as '0 7 * * *'; got %r, so the nightly run is off.", cron)
            _rejected_cron = cron
        return None
    return cron


def fire_if_due(now: Optional[datetime] = None) -> Optional[str]:
    """Submit the nightly run if a slot has passed since the app started; returns its request id."""
    global _armed_at, _handled_slot
    now = now or utcnow()
    if _armed_at is None:
        _armed_at = now
    cron = schedule()
    if cron is None:
        return None
    slot = croniter(cron, now).get_prev(datetime)
    if slot < _armed_at or slot == _handled_slot:
        return None

    from app.db.session import session_scope
    from app.services import run_service
    from app.services.audit import record_audit

    with session_scope() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.connection().exec_driver_sql("SELECT pg_advisory_xact_lock(hashtext('nightly-run'))")
        already = session.exec(select(RunRequest.id).where(
            RunRequest.request_source == RequestSource.NIGHTLY,  # type: ignore[arg-type]
            RunRequest.created_at >= slot)).first()
        if already:
            _handled_slot = slot
            return None
        payload = RunSubmit(depth=Depth.FULL, request_source=RequestSource.NIGHTLY, requested_by=REQUESTED_BY)
        try:
            request = run_service.submit_request(session, payload)
        except run_service.UnknownTargetError as exc:
            _handled_slot = slot
            logger.warning("Skipped the nightly run for %s UTC: %s", slot, exc)
            return None
        _handled_slot = slot
        record_audit(session, actor=REQUESTED_BY, action="run.trigger", entity_type="run_request",
                     entity_id=request.id, request_source=RequestSource.NIGHTLY.value,
                     summary="Submitted the nightly run (depth=full)", after=payload.model_dump(mode="json"))
        logger.info("Submitted the nightly run %s for %s UTC.", request.id, slot)
        return request.id
