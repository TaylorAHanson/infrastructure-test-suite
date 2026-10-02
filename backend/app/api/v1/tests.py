"""Test catalog endpoints (target-agnostic bundle of Checks)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db.session import get_session
from app.handlers.registry import OperationBlocked
from app.models import Check, Test
from app.schemas.schemas import TestCreate, TestUpdate
from app.services import pinned_versions, versioning, yaml_export
from app.services.audit import record_audit, version_pointer

router = APIRouter()


@router.get("")
def list_tests(session: Session = Depends(get_session)) -> list[dict]:
    return [pinned_versions.test_read(session, t) for t in session.exec(select(Test)).all()]


@router.get("/{test_id}")
def get_test(test_id: str, session: Session = Depends(get_session)) -> dict:
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    return pinned_versions.test_read(session, test)


@router.get("/{test_id}/yaml")
def export_test_yaml(test_id: str, session: Session = Depends(get_session)) -> dict:
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    return {"yaml": yaml_export.test_to_yaml(session, test)}


@router.post("", status_code=201)
def create_test(payload: TestCreate, session: Session = Depends(get_session),
                actor: str = Query("api-user")) -> dict:
    members = pinned_versions.members_in(payload.members, payload.check_ids)
    for m in members:
        if not session.get(Check, m.check_id):
            raise HTTPException(422, f"check {m.check_id} not found")
    test = Test(name=payload.name, description=payload.description, tags=payload.tags,
                run_as_role=payload.run_as_role)
    session.add(test)
    session.commit()
    session.refresh(test)
    pinned_versions.set_members(session, test.id, members)
    session.commit()
    session.refresh(test)
    pinned_versions.record_test_version(session, test, actor)
    # Recorded as a version pointer, not a duplicated diff (ADR-0014 §3): the
    # snapshot already lives in EntityVersion.
    record_audit(session, actor=actor, action="test.create", entity_type="test", entity_id=test.id,
                 summary=f"Created test {test.name}",
                 after=version_pointer("test", test.id, test.version))
    return pinned_versions.test_read(session, test)


@router.patch("/{test_id}")
def update_test(test_id: str, payload: TestUpdate, session: Session = Depends(get_session),
                actor: str = Query("api-user")) -> dict:
    """Edit a Test — fields, membership and member Check content — as a new version (#118)."""
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    try:
        pinned_versions.edit_test(session, test, payload, actor)
    except (OperationBlocked, ValueError) as exc:
        raise HTTPException(422, str(exc))
    return pinned_versions.test_read(session, test)


@router.post("/{test_id}/accept")
def accept_test(test_id: str, session: Session = Depends(get_session),
                actor: str = Query("api-user")) -> dict:
    """Promote a Draft Test to Active (explicit human accept, ADR-0006 §8)."""
    from app.services.assistant import accept_test as _accept

    try:
        test = _accept(session, test_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return pinned_versions.test_read(session, test)


@router.get("/{test_id}/versions")
def list_test_versions(test_id: str, session: Session = Depends(get_session)) -> list[dict]:
    versions = versioning.list_versions(session, "test", test_id)
    return [{"version": v.version, "actor": v.actor, "created_at": v.created_at, "snapshot": v.snapshot}
            for v in versions]


@router.post("/{test_id}/rollback/{version}")
def rollback_test(test_id: str, version: int, session: Session = Depends(get_session),
                  actor: str = Query("api-user")) -> dict:
    """Restore a prior Test version — incl. its pinned Check content — as a new version."""
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    try:
        pinned_versions.rollback_test(session, test, version, actor)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return pinned_versions.test_read(session, test)
