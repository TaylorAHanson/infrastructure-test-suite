"""Suite catalog endpoints (a named selection of Tests for a Run)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import actor_handle
from app.core.ids import utcnow
from app.db.session import get_session
from app.models import Suite, SuiteMember, Test
from app.schemas.schemas import SuiteCreate, SuiteUpdate
from app.services import versioning
from app.services.audit import record_audit

router = APIRouter()


def _test_ids(session: Session, suite_id: str) -> list[str]:
    rows = session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite_id).order_by(SuiteMember.sort_order)  # type: ignore[arg-type]
    ).all()
    return [m.test_id for m in rows]


def _set_members(session: Session, suite_id: str, test_ids: list[str]) -> None:
    for m in session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite_id)).all():
        session.delete(m)
    session.flush()
    for i, tid in enumerate(dict.fromkeys(test_ids)):
        session.add(SuiteMember(suite_id=suite_id, test_id=tid, sort_order=i))


def _suite_out(session: Session, suite: Suite) -> dict:
    return {**suite.model_dump(mode="json"), "test_ids": _test_ids(session, suite.id)}


@router.get("")
def list_suites(session: Session = Depends(get_session)) -> list[dict]:
    return [_suite_out(session, s) for s in session.exec(select(Suite)).all()]


@router.get("/{suite_id}")
def get_suite(suite_id: str, session: Session = Depends(get_session)) -> dict:
    suite = session.get(Suite, suite_id)
    if not suite:
        raise HTTPException(404, "suite not found")
    return _suite_out(session, suite)


@router.post("", status_code=201)
def create_suite(payload: SuiteCreate, session: Session = Depends(get_session),
                 actor: str = Depends(actor_handle())) -> dict:
    for tid in payload.test_ids:
        if not session.get(Test, tid):
            raise HTTPException(422, f"test {tid} not found")
    suite = Suite(name=payload.name, description=payload.description, tags=payload.tags)
    session.add(suite)
    session.commit()
    session.refresh(suite)
    _set_members(session, suite.id, payload.test_ids)
    session.commit()
    session.refresh(suite)
    out = _suite_out(session, suite)
    versioning.snapshot(session, "suite", suite.id, suite.version, out, actor)
    record_audit(session, actor=actor, action="suite.create", entity_type="suite", entity_id=suite.id,
                 summary=f"Created suite {suite.name}", after=out)
    return out


@router.patch("/{suite_id}")
def update_suite(suite_id: str, payload: SuiteUpdate, session: Session = Depends(get_session),
                 actor: str = Depends(actor_handle())) -> dict:
    suite = session.get(Suite, suite_id)
    if not suite:
        raise HTTPException(404, "suite not found")
    before = _suite_out(session, suite)
    data = payload.model_dump(exclude_unset=True)
    for tid in payload.test_ids or []:
        if not session.get(Test, tid):
            raise HTTPException(422, f"test {tid} not found")
    for key in ("name", "description", "tags", "enabled"):
        if key in data and data[key] is not None:
            setattr(suite, key, data[key])
    if payload.test_ids is not None:
        _set_members(session, suite.id, payload.test_ids)
    suite.version += 1
    suite.updated_at = utcnow()
    session.add(suite)
    session.commit()
    session.refresh(suite)
    out = _suite_out(session, suite)
    versioning.snapshot(session, "suite", suite.id, suite.version, out, actor)
    record_audit(session, actor=actor, action="suite.update", entity_type="suite", entity_id=suite.id,
                 summary=f"Updated suite {suite.name}", before=before, after=out)
    return out


@router.delete("/{suite_id}", status_code=204, response_model=None)
def delete_suite(suite_id: str, session: Session = Depends(get_session),
                 actor: str = Depends(actor_handle())) -> None:
    """Delete a Suite (its Tests and their history are untouched)."""
    suite = session.get(Suite, suite_id)
    if not suite:
        raise HTTPException(404, "suite not found")
    before = _suite_out(session, suite)
    for m in session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite_id)).all():
        session.delete(m)
    session.delete(suite)
    session.commit()
    record_audit(session, actor=actor, action="suite.delete", entity_type="suite", entity_id=suite_id,
                 summary=f"Deleted suite {before['name']}", before=before)
