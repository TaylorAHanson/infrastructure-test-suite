"""Run submission, history, and detail endpoints.

Submission is async + idempotent: it returns a PENDING request immediately; the
poller (or a Databricks Job) executes it. ``?inline=true`` forces synchronous
execution, useful for local dev and CI.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.core.enums import RunStatus, TestCategory
from app.db.session import get_session
from app.models import RunRequest, SuiteMember, TestDefinition, TestRun, TestRunStep
from app.schemas.schemas import RunRequestOut, RunSubmit
from app.services import run_service
from app.services.audit import record_audit

router = APIRouter()


@router.post("", response_model=RunRequestOut, status_code=202)
def submit_run(
    payload: RunSubmit,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    inline: bool = Query(False, description="Process synchronously instead of via the poller."),
) -> RunRequestOut:
    request = run_service.submit_request(session, payload)
    record_audit(
        session, actor=payload.requested_by, action="run.trigger", entity_type="run_request",
        entity_id=request.id, request_source=payload.request_source,
        summary=f"Submitted run request ({payload.execution_mode.value})",
        after=payload.model_dump(mode="json"),
    )
    run_ids: list[str] = []
    if inline:
        run_service.process_request(request.id, worker_id="inline-api")
        run_ids = [r.run_id for r in session.exec(
            select(TestRun).where(TestRun.request_id == request.id)
        ).all()]
    else:
        # The poller will pick up the PENDING request. We schedule a no-op
        # background nudge so tests without a poller still complete promptly.
        background.add_task(run_service.process_request, request.id, "bg-nudge")
    return RunRequestOut(
        id=request.id, status=request.status.value, execution_mode=request.execution_mode,
        requested_by=request.requested_by, created_at=request.created_at, run_ids=run_ids,
    )


@router.get("/requests")
def list_requests(session: Session = Depends(get_session), limit: int = 50) -> list[RunRequest]:
    return session.exec(
        select(RunRequest).order_by(RunRequest.created_at.desc()).limit(limit)  # type: ignore[union-attr]
    ).all()


@router.get("/requests/{request_id}")
def get_request(request_id: str, session: Session = Depends(get_session)) -> dict:
    req = session.get(RunRequest, request_id)
    if not req:
        raise HTTPException(404, "run request not found")
    runs = session.exec(select(TestRun).where(TestRun.request_id == request_id)).all()
    return {**req.model_dump(), "run_ids": [r.run_id for r in runs]}


@router.get("")
def list_runs(
    session: Session = Depends(get_session),
    status: Optional[RunStatus] = None,
    category: Optional[TestCategory] = None,
    catalog: Optional[str] = None,
    target_id: Optional[str] = None,
    test_definition_id: Optional[str] = None,
    suite_id: Optional[str] = None,
    requester: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 200,
) -> list[dict]:
    stmt = select(TestRun)
    if status:
        stmt = stmt.where(TestRun.status == status)
    if catalog:
        stmt = stmt.where(TestRun.catalog == catalog)
    if target_id:
        stmt = stmt.where(TestRun.target_id == target_id)
    if test_definition_id:
        stmt = stmt.where(TestRun.test_definition_id == test_definition_id)
    if requester:
        stmt = stmt.where(TestRun.requested_by == requester)
    if since:
        stmt = stmt.where(TestRun.created_at >= since)
    if until:
        stmt = stmt.where(TestRun.created_at <= until)
    stmt = stmt.order_by(TestRun.created_at.desc()).limit(limit)  # type: ignore[union-attr]
    runs = session.exec(stmt).all()

    # Category / suite filters resolved via definitions (portable across DBs).
    defs = {d.id: d for d in session.exec(select(TestDefinition)).all()}
    if category:
        runs = [r for r in runs if defs.get(r.test_definition_id) and defs[r.test_definition_id].category == category]
    if suite_id:
        member_ids = {m.test_definition_id for m in session.exec(
            select(SuiteMember).where(SuiteMember.suite_id == suite_id)
        ).all()}
        runs = [r for r in runs if r.test_definition_id in member_ids]

    out = []
    for r in runs:
        d = defs.get(r.test_definition_id)
        out.append({
            **r.model_dump(),
            "test_name": d.display_name if d else r.test_definition_id,
            "category": d.category.value if d else None,
        })
    return out


@router.get("/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    run = session.get(TestRun, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    steps = session.exec(
        select(TestRunStep).where(TestRunStep.run_id == run_id).order_by(TestRunStep.sort_order)  # type: ignore[arg-type]
    ).all()
    d = session.get(TestDefinition, run.test_definition_id)
    return {
        **run.model_dump(),
        "test_name": d.display_name if d else run.test_definition_id,
        "category": d.category.value if d else None,
        "executor_key": d.executor_key if d else None,
        "steps": [s.model_dump() for s in steps],
    }
