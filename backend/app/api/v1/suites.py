"""Suites catalog endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.core.ids import utcnow
from app.db.session import get_session
from app.models import SuiteMember, TestSuite
from app.schemas.schemas import SuiteCreate, SuiteUpdate
from app.services.audit import record_audit

router = APIRouter()


def _members(session: Session, suite_id: str) -> list[str]:
    rows = session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite_id).order_by(SuiteMember.sort_order)  # type: ignore[arg-type]
    ).all()
    return [m.test_definition_id for m in rows]


@router.get("")
def list_suites(session: Session = Depends(get_session)) -> list[dict]:
    suites = session.exec(select(TestSuite)).all()
    return [{**s.model_dump(), "test_definition_ids": _members(session, s.id)} for s in suites]


@router.get("/{suite_id}")
def get_suite(suite_id: str, session: Session = Depends(get_session)) -> dict:
    s = session.get(TestSuite, suite_id)
    if not s:
        raise HTTPException(404, "suite not found")
    return {**s.model_dump(), "test_definition_ids": _members(session, s.id)}


@router.post("", status_code=201)
def create_suite(payload: SuiteCreate, session: Session = Depends(get_session),
                 actor: str = Query("api-user")) -> dict:
    s = TestSuite(name=payload.name, description=payload.description, tags=payload.tags)
    session.add(s)
    session.commit()
    session.refresh(s)
    for i, tid in enumerate(payload.test_definition_ids):
        session.add(SuiteMember(suite_id=s.id, test_definition_id=tid, sort_order=i))
    session.commit()
    record_audit(session, actor=actor, action="suite.create", entity_type="suite", entity_id=s.id,
                 summary=f"Created suite {s.name}", after=payload.model_dump(mode="json"))
    return {**s.model_dump(), "test_definition_ids": payload.test_definition_ids}


@router.patch("/{suite_id}")
def update_suite(suite_id: str, payload: SuiteUpdate, session: Session = Depends(get_session),
                 actor: str = Query("api-user")) -> dict:
    s = session.get(TestSuite, suite_id)
    if not s:
        raise HTTPException(404, "suite not found")
    before = {**s.model_dump(mode="json"), "test_definition_ids": _members(session, s.id)}
    data = payload.model_dump(exclude_unset=True)
    member_ids = data.pop("test_definition_ids", None)
    for key, value in data.items():
        setattr(s, key, value)
    s.updated_at = utcnow()
    session.add(s)
    if member_ids is not None:
        for m in session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite_id)).all():
            session.delete(m)
        for i, tid in enumerate(member_ids):
            session.add(SuiteMember(suite_id=suite_id, test_definition_id=tid, sort_order=i))
    session.commit()
    session.refresh(s)
    record_audit(session, actor=actor, action="suite.update", entity_type="suite", entity_id=s.id,
                 summary=f"Updated suite {s.name}", before=before,
                 after={**s.model_dump(mode="json"), "test_definition_ids": _members(session, s.id)})
    return {**s.model_dump(), "test_definition_ids": _members(session, s.id)}
