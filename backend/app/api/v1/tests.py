"""Test definitions catalog endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.core.enums import RequestSource, TestCategory
from app.db.session import get_session
from app.executors.registry import registry
from app.models import TestDefinition
from app.schemas.schemas import TestDefinitionCreate, TestDefinitionUpdate
from app.services.audit import record_audit

router = APIRouter()


@router.get("")
def list_tests(
    session: Session = Depends(get_session),
    category: Optional[TestCategory] = None,
    q: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> list[TestDefinition]:
    stmt = select(TestDefinition)
    if category:
        stmt = stmt.where(TestDefinition.category == category)
    if enabled is not None:
        stmt = stmt.where(TestDefinition.enabled == enabled)
    rows = session.exec(stmt).all()
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r.display_name.lower() or any(ql in t.lower() for t in r.tags)]
    return rows


@router.get("/registry")
def list_registry() -> dict:
    """Executor keys currently registered (live plugins vs stubs)."""
    return {"registered_executor_keys": sorted(registry.keys())}


@router.get("/{test_id}")
def get_test(test_id: str, session: Session = Depends(get_session)) -> TestDefinition:
    d = session.get(TestDefinition, test_id)
    if not d:
        raise HTTPException(404, "test definition not found")
    return d


@router.post("", status_code=201)
def create_test(
    payload: TestDefinitionCreate,
    session: Session = Depends(get_session),
    actor: str = Query("api-user"),
) -> TestDefinition:
    d = TestDefinition(**payload.model_dump())
    session.add(d)
    session.commit()
    session.refresh(d)
    record_audit(session, actor=actor, action="test.create", entity_type="test",
                 entity_id=d.id, summary=f"Created test {d.display_name}", after=payload.model_dump(mode="json"))
    return d


@router.patch("/{test_id}")
def update_test(
    test_id: str,
    payload: TestDefinitionUpdate,
    session: Session = Depends(get_session),
    actor: str = Query("api-user"),
) -> TestDefinition:
    d = session.get(TestDefinition, test_id)
    if not d:
        raise HTTPException(404, "test definition not found")
    before = d.model_dump(mode="json")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(d, key, value)
    from app.core.ids import utcnow

    d.updated_at = utcnow()
    session.add(d)
    session.commit()
    session.refresh(d)
    record_audit(session, actor=actor, action="test.update", entity_type="test", entity_id=d.id,
                 summary=f"Updated test {d.display_name}", before=before, after=d.model_dump(mode="json"))
    return d
