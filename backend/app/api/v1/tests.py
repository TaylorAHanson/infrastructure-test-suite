"""Test catalog endpoints (target-agnostic bundle of Checks)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.core.ids import utcnow
from app.db.session import get_session
from app.models import Check, Test, TestCheck
from app.schemas.schemas import TestCheckIn, TestCreate, TestUpdate
from app.services import versioning, yaml_export
from app.services.audit import record_audit

router = APIRouter()


def _members_in(payload_members: list[TestCheckIn], check_ids: list[str]) -> list[TestCheckIn]:
    if payload_members:
        return payload_members
    return [TestCheckIn(check_id=cid) for cid in check_ids]


def _set_members(session: Session, test_id: str, members: list[TestCheckIn]) -> None:
    for m in session.exec(select(TestCheck).where(TestCheck.test_id == test_id)).all():
        session.delete(m)
    for i, m in enumerate(members):
        session.add(TestCheck(
            test_id=test_id, check_id=m.check_id, sort_order=i, kind=m.kind,
            run_as_role=m.run_as_role, counterpart_check_id=m.counterpart_check_id,
        ))


def _test_out(session: Session, test: Test) -> dict:
    rows = session.exec(
        select(TestCheck).where(TestCheck.test_id == test.id).order_by(TestCheck.sort_order)  # type: ignore[arg-type]
    ).all()
    return {
        **test.model_dump(mode="json"),
        "members": [
            {"check_id": m.check_id, "kind": m.kind, "run_as_role": m.run_as_role,
             "counterpart_check_id": m.counterpart_check_id, "sort_order": m.sort_order}
            for m in rows
        ],
    }


@router.get("")
def list_tests(session: Session = Depends(get_session)) -> list[dict]:
    return [_test_out(session, t) for t in session.exec(select(Test)).all()]


@router.get("/{test_id}")
def get_test(test_id: str, session: Session = Depends(get_session)) -> dict:
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    return _test_out(session, test)


@router.get("/{test_id}/yaml")
def export_test_yaml(test_id: str, session: Session = Depends(get_session)) -> dict:
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    return {"yaml": yaml_export.test_to_yaml(session, test)}


@router.post("", status_code=201)
def create_test(payload: TestCreate, session: Session = Depends(get_session),
                actor: str = Query("api-user")) -> dict:
    members = _members_in(payload.members, payload.check_ids)
    for m in members:
        if not session.get(Check, m.check_id):
            raise HTTPException(422, f"check {m.check_id} not found")
    test = Test(name=payload.name, description=payload.description, tags=payload.tags,
                run_as_role=payload.run_as_role)
    session.add(test)
    session.commit()
    session.refresh(test)
    _set_members(session, test.id, members)
    session.commit()
    session.refresh(test)
    out = _test_out(session, test)
    versioning.snapshot(session, "test", test.id, test.version, out, actor)
    record_audit(session, actor=actor, action="test.create", entity_type="test", entity_id=test.id,
                 summary=f"Created test {test.name}", after=out)
    return out


@router.patch("/{test_id}")
def update_test(test_id: str, payload: TestUpdate, session: Session = Depends(get_session),
                actor: str = Query("api-user")) -> dict:
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    before = _test_out(session, test)
    data = payload.model_dump(exclude_unset=True)
    for key in ("name", "description", "tags", "run_as_role", "enabled"):
        if key in data and data[key] is not None:
            setattr(test, key, data[key])
    if payload.members is not None or payload.check_ids is not None:
        _set_members(session, test.id, _members_in(payload.members or [], payload.check_ids or []))
    test.version += 1
    test.updated_at = utcnow()
    session.add(test)
    session.commit()
    session.refresh(test)
    out = _test_out(session, test)
    versioning.snapshot(session, "test", test.id, test.version, out, actor)
    record_audit(session, actor=actor, action="test.update", entity_type="test", entity_id=test.id,
                 summary=f"Updated test {test.name}", before=before, after=out)
    return out


@router.post("/{test_id}/accept")
def accept_test(test_id: str, session: Session = Depends(get_session),
                actor: str = Query("api-user")) -> dict:
    """Promote a Draft Test to Active (explicit human accept, ADR-0006 §8)."""
    from app.services.assistant import accept_test as _accept

    try:
        test = _accept(session, test_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return _test_out(session, test)


@router.get("/{test_id}/versions")
def list_test_versions(test_id: str, session: Session = Depends(get_session)) -> list[dict]:
    versions = versioning.list_versions(session, "test", test_id)
    return [{"version": v.version, "actor": v.actor, "created_at": v.created_at, "snapshot": v.snapshot}
            for v in versions]


@router.post("/{test_id}/rollback/{version}")
def rollback_test(test_id: str, version: int, session: Session = Depends(get_session),
                  actor: str = Query("api-user")) -> dict:
    """Restore a prior Test version as a new current version (ADR-0001)."""
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    snap = versioning.get_version(session, "test", test_id, version)
    if not snap:
        raise HTTPException(404, "version not found")
    before = _test_out(session, test)
    data = snap.snapshot
    for key in ("name", "description", "tags", "run_as_role", "status", "origin",
                "derivation", "enabled"):
        if key in data:
            setattr(test, key, data[key])
    if isinstance(data.get("members"), list):
        _set_members(session, test.id, [
            TestCheckIn(check_id=m["check_id"], kind=m.get("kind", "assertion"),
                        run_as_role=m.get("run_as_role"),
                        counterpart_check_id=m.get("counterpart_check_id"))
            for m in data["members"]
        ])
    test.version += 1
    test.updated_at = utcnow()
    session.add(test)
    session.commit()
    session.refresh(test)
    out = _test_out(session, test)
    versioning.snapshot(session, "test", test.id, test.version, out, actor)
    record_audit(session, actor=actor, action="test.rollback", entity_type="test", entity_id=test.id,
                 summary=f"Rolled test back to v{version}", before=before, after=out)
    return out
