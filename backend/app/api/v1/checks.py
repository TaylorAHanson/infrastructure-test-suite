"""Check catalog endpoints (the atomic assertion unit)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.core.ids import utcnow
from app.db.session import get_session
from app.executors.registry import OperationBlocked
from app.models import Check
from app.schemas.schemas import CheckCreate, CheckUpdate, CompileRequest
from app.services import versioning
from app.services.assertion_compiler import compile_assertion
from app.services.audit import record_audit
from app.services.authoring import apply_check_update, build_check_fields

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


@router.post("/compile")
def compile_preview(payload: CompileRequest) -> dict:
    """Preview how a plain-text assertion compiles (deterministic vs agentic)."""
    return compile_assertion(payload.assertion).to_dict()


@router.get("/{check_id}")
def get_check(check_id: str, session: Session = Depends(get_session)) -> Check:
    check = session.get(Check, check_id)
    if not check:
        raise HTTPException(404, "check not found")
    return check


@router.post("", status_code=201)
def create_check(
    payload: CheckCreate,
    session: Session = Depends(get_session),
    actor: str = Query("api-user"),
) -> dict:
    try:
        fields = build_check_fields(payload)
    except OperationBlocked as exc:
        raise HTTPException(422, str(exc))
    check = Check(**fields)
    session.add(check)
    session.commit()
    session.refresh(check)
    out = check.model_dump(mode="json")  # capture before follow-up commits expire the row
    versioning.snapshot(session, "check", check.id, check.version, out, actor)
    record_audit(session, actor=actor, action="check.create", entity_type="check", entity_id=check.id,
                 summary=f"Created check {out['display_name']}", after=out)
    return out


@router.patch("/{check_id}")
def update_check(
    check_id: str,
    payload: CheckUpdate,
    session: Session = Depends(get_session),
    actor: str = Query("api-user"),
) -> dict:
    check = session.get(Check, check_id)
    if not check:
        raise HTTPException(404, "check not found")
    before = check.model_dump(mode="json")
    try:
        fields = apply_check_update(payload)
    except OperationBlocked as exc:
        raise HTTPException(422, str(exc))
    for key, value in fields.items():
        setattr(check, key, value)
    check.version += 1
    check.updated_at = utcnow()
    session.add(check)
    session.commit()
    session.refresh(check)
    out = check.model_dump(mode="json")
    versioning.snapshot(session, "check", check.id, check.version, out, actor)
    record_audit(session, actor=actor, action="check.update", entity_type="check", entity_id=check.id,
                 summary=f"Updated check {out['display_name']}", before=before, after=out)
    return out


@router.get("/{check_id}/versions")
def list_check_versions(check_id: str, session: Session = Depends(get_session)) -> list[dict]:
    versions = versioning.list_versions(session, "check", check_id)
    return [{"version": v.version, "actor": v.actor, "created_at": v.created_at, "snapshot": v.snapshot}
            for v in versions]


@router.post("/{check_id}/rollback/{version}")
def rollback_check(
    check_id: str,
    version: int,
    session: Session = Depends(get_session),
    actor: str = Query("api-user"),
) -> dict:
    check = session.get(Check, check_id)
    if not check:
        raise HTTPException(404, "check not found")
    snap = versioning.get_version(session, "check", check_id, version)
    if not snap:
        raise HTTPException(404, "version not found")
    before = check.model_dump(mode="json")
    restorable = {k: v for k, v in snap.snapshot.items()
                  if hasattr(check, k) and k not in ("id", "version", "created_at")}
    for key, value in restorable.items():
        setattr(check, key, value)
    check.version += 1
    check.updated_at = utcnow()
    session.add(check)
    session.commit()
    session.refresh(check)
    out = check.model_dump(mode="json")
    versioning.snapshot(session, "check", check.id, check.version, out, actor)
    record_audit(session, actor=actor, action="check.rollback", entity_type="check", entity_id=check.id,
                 summary=f"Rolled check back to v{version}", before=before, after=out)
    return out
