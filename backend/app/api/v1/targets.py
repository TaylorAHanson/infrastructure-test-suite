"""Targets and catalog selector endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import TestTarget
from app.schemas.schemas import TargetCreate
from app.services.audit import record_audit

router = APIRouter()


@router.get("")
def list_targets(session: Session = Depends(get_session)) -> list[TestTarget]:
    return session.exec(select(TestTarget)).all()


@router.get("/catalogs")
def list_catalogs(session: Session = Depends(get_session)) -> list[str]:
    """Distinct catalogs across targets - drives the 'all/partial catalog' selector."""
    rows = session.exec(select(TestTarget)).all()
    return sorted({t.catalog for t in rows if t.catalog})


@router.post("", status_code=201)
def create_target(payload: TargetCreate, session: Session = Depends(get_session),
                  actor: str = Query("api-user")) -> TestTarget:
    t = TestTarget(**payload.model_dump())
    session.add(t)
    session.commit()
    session.refresh(t)
    record_audit(session, actor=actor, action="target.create", entity_type="target", entity_id=t.id,
                 summary=f"Created target {t.name}", after=payload.model_dump(mode="json"))
    return t


@router.delete("/{target_id}")
def delete_target(target_id: str, session: Session = Depends(get_session),
                  actor: str = Query("api-user")) -> dict:
    t = session.get(TestTarget, target_id)
    if not t:
        raise HTTPException(404, "target not found")
    session.delete(t)
    session.commit()
    record_audit(session, actor=actor, action="target.delete", entity_type="target", entity_id=target_id,
                 summary=f"Deleted target {t.name}")
    return {"deleted": target_id}
