"""Run submission, history, and detail endpoints (the Run API, ADR-0005).

Submission is async + idempotent; ``?inline=true`` forces synchronous execution
(local dev / CI). A Run fans out to one CheckRun per (Check × Target); the
detail endpoint returns a CheckRun's **stage outcomes on the canonical spine**
plus the **derived** connectable/functional view (never a stored column).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.core.stages import derive_connectivity
from app.core.taxonomy import bucket_at_stage
from app.db.session import get_session
from app.models import Check, CheckRun, RunRequest, StageOutcome
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
    record_audit(session, actor=payload.requested_by, action="run.trigger", entity_type="run_request",
                 entity_id=request.id, request_source=payload.request_source.value,
                 summary=f"Submitted run request (depth={payload.depth.value})",
                 after=payload.model_dump(mode="json"))
    run_ids: list[str] = []
    if inline:
        run_service.process_request(request.id, worker_id="inline-api")
        run_ids = [r.run_id for r in session.exec(
            select(CheckRun).where(CheckRun.request_id == request.id)
        ).all()]
    else:
        background.add_task(run_service.process_request, request.id, "bg-nudge")
    session.refresh(request)
    return RunRequestOut(
        id=request.id, status=request.status.value, depth=request.depth,
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
    runs = session.exec(select(CheckRun).where(CheckRun.request_id == request_id)).all()
    return {
        **req.model_dump(mode="json"),
        "run_ids": [r.run_id for r in runs],
        "verdict": run_service.request_verdict(runs).value,
        "test_rollup": run_service.test_rollup(runs),
    }


@router.get("")
def list_runs(
    session: Session = Depends(get_session),
    request_id: Optional[str] = None,
    check_id: Optional[str] = None,
    target_id: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    stmt = select(CheckRun)
    if request_id:
        stmt = stmt.where(CheckRun.request_id == request_id)
    if check_id:
        stmt = stmt.where(CheckRun.check_id == check_id)
    if target_id:
        stmt = stmt.where(CheckRun.target_id == target_id)
    stmt = stmt.order_by(CheckRun.created_at.desc()).limit(limit)  # type: ignore[union-attr]
    runs = session.exec(stmt).all()
    checks = {c.id: c for c in session.exec(select(Check)).all()}
    return [
        {
            **r.model_dump(mode="json"),
            "check_name": checks[r.check_id].display_name if r.check_id in checks else r.check_id,
            "bucket_at_stage": bucket_at_stage(r.failure_bucket, r.failure_stage) if r.failure_bucket else None,
        }
        for r in runs
    ]


@router.get("/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    run = session.get(CheckRun, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    stages = session.exec(
        select(StageOutcome).where(StageOutcome.run_id == run_id).order_by(StageOutcome.sort_order)  # type: ignore[arg-type]
    ).all()
    check = session.get(Check, run.check_id)
    connectivity = derive_connectivity(stages)
    return {
        **run.model_dump(mode="json"),
        "check_name": check.display_name if check else run.check_id,
        "handler_key": check.handler_key if check else None,
        "stage_outcomes": [s.model_dump(mode="json") for s in stages],
        "connectable": connectivity["connectable"],
        "functional": connectivity["functional"],
        "bucket_at_stage": bucket_at_stage(run.failure_bucket, run.failure_stage) if run.failure_bucket else None,
    }
