"""Schedule management.

The app does **not** run an in-process cron as the primary scheduler. Instead a
schedule is materialised as a Databricks Job (cron) via the SDK / bundle; each
fire creates a ``run_request`` row and the suite-runner job executes the probes.

For local dev (no workspace) we also expose :func:`due_schedules` so a tiny
fallback tick can drive demos - this is explicitly the non-authoritative path.
"""
from __future__ import annotations

import logging
from typing import Optional

from croniter import croniter
from sqlmodel import Session, select

from app.clients.databricks import upsert_cron_job
from app.core.enums import RequestSource, RunStatus
from app.core.ids import utcnow
from app.models import RunRequest, Schedule
from app.schemas.schemas import ScheduleCreate, ScheduleUpdate

logger = logging.getLogger(__name__)


def create_schedule(session: Session, payload: ScheduleCreate, actor: str) -> Schedule:
    schedule = Schedule(
        name=payload.name,
        cron=payload.cron,
        timezone=payload.timezone,
        enabled=payload.enabled,
        suite_ids=payload.suite_ids,
        test_definition_ids=payload.test_definition_ids,
        target_ids=payload.target_ids,
        catalogs=payload.catalogs,
        execution_mode=payload.execution_mode,
        created_by=actor,
    )
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    _sync_to_databricks(schedule)
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


def update_schedule(session: Session, schedule: Schedule, payload: ScheduleUpdate) -> Schedule:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(schedule, key, value)
    schedule.updated_at = utcnow()
    _sync_to_databricks(schedule)
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


def _sync_to_databricks(schedule: Schedule) -> None:
    params = {"schedule_id": schedule.id, "execution_mode": schedule.execution_mode.value}
    job_id = upsert_cron_job(
        name=schedule.name,
        cron=schedule.cron,
        timezone=schedule.timezone,
        params=params,
        job_id=schedule.databricks_job_id,
    )
    if job_id:
        schedule.databricks_job_id = job_id


def fire_schedule(session: Session, schedule: Schedule) -> RunRequest:
    """Create a run_request from a schedule (used by the bundle job or fallback tick)."""
    request = RunRequest(
        requested_by=f"schedule:{schedule.name}",
        request_source=RequestSource.SCHEDULE,
        suite_ids=schedule.suite_ids,
        test_definition_ids=schedule.test_definition_ids,
        target_ids=schedule.target_ids,
        catalogs=schedule.catalogs,
        execution_mode=schedule.execution_mode,
        schedule_id=schedule.id,
        status=RunStatus.PENDING,
    )
    session.add(request)
    schedule.last_fired_at = utcnow()
    session.add(schedule)
    session.commit()
    session.refresh(request)
    return request


def due_schedules(session: Session, now=None) -> list[Schedule]:
    """Return enabled schedules whose cron is due since ``last_fired_at``.

    Local/demo fallback only. In production the Databricks Job owns timing.
    """
    now = now or utcnow()
    out: list[Schedule] = []
    schedules = session.exec(select(Schedule).where(Schedule.enabled == True)).all()  # noqa: E712
    for s in schedules:
        base = s.last_fired_at or s.created_at
        try:
            nxt = croniter(s.cron, base).get_next(type(now))
        except Exception:  # noqa: BLE001 - bad cron string
            continue
        if nxt <= now:
            out.append(s)
    return out
