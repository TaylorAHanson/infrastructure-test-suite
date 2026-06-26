"""Schedule endpoints. Schedules materialise as Databricks Jobs (cron)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import Schedule
from app.schemas.schemas import ScheduleCreate, ScheduleUpdate
from app.services import schedule_service
from app.services.audit import record_audit

router = APIRouter()


@router.get("")
def list_schedules(session: Session = Depends(get_session)) -> list[Schedule]:
    return session.exec(select(Schedule)).all()


@router.post("", status_code=201)
def create_schedule(payload: ScheduleCreate, session: Session = Depends(get_session),
                    actor: str = Query("api-user")) -> Schedule:
    s = schedule_service.create_schedule(session, payload, actor)
    record_audit(session, actor=actor, action="schedule.create", entity_type="schedule", entity_id=s.id,
                 summary=f"Created schedule {s.name} ({s.cron})", after=payload.model_dump(mode="json"))
    return s


@router.patch("/{schedule_id}")
def update_schedule(schedule_id: str, payload: ScheduleUpdate, session: Session = Depends(get_session),
                    actor: str = Query("api-user")) -> Schedule:
    s = session.get(Schedule, schedule_id)
    if not s:
        raise HTTPException(404, "schedule not found")
    before = s.model_dump(mode="json")
    s = schedule_service.update_schedule(session, s, payload)
    record_audit(session, actor=actor, action="schedule.update", entity_type="schedule", entity_id=s.id,
                 summary=f"Updated schedule {s.name}", before=before, after=s.model_dump(mode="json"))
    return s


@router.post("/{schedule_id}/fire")
def fire_schedule(schedule_id: str, session: Session = Depends(get_session),
                  actor: str = Query("api-user")) -> dict:
    """Manually fire a schedule now (creates a run_request via the normal path)."""
    s = session.get(Schedule, schedule_id)
    if not s:
        raise HTTPException(404, "schedule not found")
    req = schedule_service.fire_schedule(session, s)
    record_audit(session, actor=actor, action="schedule.fire", entity_type="schedule", entity_id=s.id,
                 summary=f"Manually fired schedule {s.name}")
    return {"request_id": req.id, "status": req.status.value}


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: str, session: Session = Depends(get_session),
                    actor: str = Query("api-user")) -> dict:
    s = session.get(Schedule, schedule_id)
    if not s:
        raise HTTPException(404, "schedule not found")
    session.delete(s)
    session.commit()
    record_audit(session, actor=actor, action="schedule.delete", entity_type="schedule", entity_id=schedule_id,
                 summary=f"Deleted schedule {s.name}")
    return {"deleted": schedule_id}
