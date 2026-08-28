"""Target registry endpoints (workspaces under test)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import Target
from app.schemas.schemas import TargetCreate
from app.services.audit import record_audit

router = APIRouter()


@router.get("")
def list_targets(session: Session = Depends(get_session)) -> list[Target]:
    return session.exec(select(Target)).all()


@router.get("/{target_id}")
def get_target(target_id: str, session: Session = Depends(get_session)) -> Target:
    target = session.get(Target, target_id)
    if not target:
        raise HTTPException(404, "target not found")
    return target


@router.post("", status_code=201)
def create_target(payload: TargetCreate, session: Session = Depends(get_session),
                  actor: str = Query("api-user")) -> dict:
    target = Target(**payload.model_dump())
    session.add(target)
    session.commit()
    session.refresh(target)
    out = target.model_dump(mode="json")
    record_audit(session, actor=actor, action="target.register", entity_type="target", entity_id=target.id,
                 summary=f"Registered target {out['name']}", after=out)
    return out
