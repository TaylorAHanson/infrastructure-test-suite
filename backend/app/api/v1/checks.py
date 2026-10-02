"""Check catalog endpoints, read-only: a Check is a script Test's ``with check(...)`` block."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import Check

router = APIRouter()


@router.get("")
def list_checks(
    session: Session = Depends(get_session),
    q: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> list[Check]:
    stmt = select(Check)
    if enabled is not None:
        stmt = stmt.where(Check.enabled == enabled)
    rows = session.exec(stmt).all()
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r.display_name.lower()]
    return rows


@router.get("/{check_id}")
def get_check(check_id: str, session: Session = Depends(get_session)) -> Check:
    check = session.get(Check, check_id)
    if not check:
        raise HTTPException(404, "check not found")
    return check
