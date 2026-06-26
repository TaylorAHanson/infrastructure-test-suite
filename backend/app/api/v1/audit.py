"""Audit log endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import AuditEvent

router = APIRouter()


@router.get("")
def list_audit(
    session: Session = Depends(get_session),
    actor: Optional[str] = None,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    limit: int = Query(200, le=1000),
) -> list[AuditEvent]:
    stmt = select(AuditEvent)
    if actor:
        stmt = stmt.where(AuditEvent.actor == actor)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    stmt = stmt.order_by(AuditEvent.ts.desc()).limit(limit)  # type: ignore[union-attr]
    return session.exec(stmt).all()
