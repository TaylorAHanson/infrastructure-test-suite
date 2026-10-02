"""Test catalog endpoints: script Tests, their versions, and Draft → Active."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.api.deps import actor_handle
from app.db.session import get_session
from app.models import Check, Test
from app.schemas.schemas import ScriptSave, ScriptTestCreate, TestUpdate
from app.script_runtime.analysis import analyze
from app.services import pinned_versions, script_tests, versioning
from app.services.audit import record_audit

router = APIRouter()


def _invalid(exc: script_tests.ScriptInvalid) -> HTTPException:
    return HTTPException(422, {"message": str(exc), "analysis": exc.analysis})


@router.post("/script", status_code=201)
def create_script_test(payload: ScriptTestCreate, session: Session = Depends(get_session),
                       actor: str = Depends(actor_handle())) -> dict:
    """Create a script Test as a Draft (the starter template if no script is given)."""
    try:
        test, _ = script_tests.create_script_test(
            session, name=payload.name, actor=actor, script=payload.script,
            description=payload.description, tags=payload.tags, run_as_role=payload.run_as_role)
    except script_tests.ScriptInvalid as exc:
        raise _invalid(exc)
    return pinned_versions.test_read(session, test)


@router.delete("/{test_id}", status_code=204)
def delete_draft_test(test_id: str, session: Session = Depends(get_session),
                      actor: str = Depends(actor_handle())) -> None:
    """Delete a Draft Test. An Active Test has run history; disable it instead."""
    from app.core.enums import TestStatus
    from app.models import CheckRun, SuiteMember, TestCheck

    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    if test.status != TestStatus.DRAFT or session.exec(
            select(CheckRun).where(CheckRun.test_id == test_id)).first() is not None:
        raise HTTPException(409, "only a Draft that has never run can be deleted; disable it instead")
    before = pinned_versions.test_read(session, test)
    members = session.exec(select(TestCheck).where(TestCheck.test_id == test_id)).all()
    owned = [m.check_id for m in members]
    owned += [c.id for c in script_tests._script_checks(session, test_id).values() if c.id not in owned]
    for m in members:
        session.delete(m)
    for m in session.exec(select(SuiteMember).where(SuiteMember.test_id == test_id)).all():
        session.delete(m)
    session.flush()
    for check_id in owned:
        check = session.get(Check, check_id)
        shared = session.exec(select(TestCheck).where(TestCheck.check_id == check_id)).first()
        ran = session.exec(select(CheckRun).where(CheckRun.check_id == check_id)).first()
        if check is not None and shared is None and ran is None:
            session.delete(check)
    session.delete(test)
    session.commit()
    record_audit(session, actor=actor, action="test.delete", entity_type="test", entity_id=test_id,
                 summary=f"Deleted draft test {before['name']}", before=before)


@router.get("/{test_id}/script")
def get_test_script(test_id: str, session: Session = Depends(get_session)) -> dict:
    """The Test's script, with its static analysis."""
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    script = script_tests.script_for(test)
    return {"script": script, "analysis": analyze(script)}


@router.put("/{test_id}/script")
def save_test_script(test_id: str, payload: ScriptSave, session: Session = Depends(get_session),
                     actor: str = Depends(actor_handle())) -> dict:
    """Save the script as the Test's next version."""
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    try:
        test, analysis = script_tests.save_script(
            session, test, payload.script, actor, name=payload.name, description=payload.description,
            tags=payload.tags, run_as_role=payload.run_as_role)
    except script_tests.ScriptInvalid as exc:
        raise _invalid(exc)
    return {**pinned_versions.test_read(session, test), "analysis": analysis}


@router.get("")
def list_tests(session: Session = Depends(get_session),
               include_retired: bool = Query(False, description="Include Tests a catalog upgrade retired.")) -> list[dict]:
    from app.services.seed.upgrade import is_retired

    return [pinned_versions.test_read(session, t) for t in session.exec(select(Test)).all()
            if include_retired or not is_retired(t)]


@router.get("/{test_id}")
def get_test(test_id: str, session: Session = Depends(get_session)) -> dict:
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    return pinned_versions.test_read(session, test)


@router.patch("/{test_id}")
def update_test(test_id: str, payload: TestUpdate, session: Session = Depends(get_session),
                actor: str = Depends(actor_handle())) -> dict:
    """Edit a Test's fields as a new version; its checks are edited in its script."""
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    pinned_versions.edit_test(session, test, payload, actor)
    return pinned_versions.test_read(session, test)


@router.post("/{test_id}/accept")
def accept_test(test_id: str, session: Session = Depends(get_session),
                actor: str = Depends(actor_handle())) -> dict:
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
                  actor: str = Depends(actor_handle())) -> dict:
    """Restore a prior Test version — incl. its pinned Check content — as a new version."""
    test = session.get(Test, test_id)
    if not test:
        raise HTTPException(404, "test not found")
    try:
        pinned_versions.rollback_test(session, test, version, actor)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return pinned_versions.test_read(session, test)
