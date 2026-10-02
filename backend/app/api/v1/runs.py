"""The shared Run API (ADR-0005/0011) — one engine behind CI, GUI, and nightly.

The contract is **async**: ``POST /runs`` starts a run and returns a ``run_id``
(``202``); the caller polls ``GET /runs/{run_id}`` until the phase is terminal,
**owning its own timeout** (the server never holds the connection open). Gating
is fail-closed — only ``PASS`` passes; ``FAIL`` / ``BLOCKED_BY_SAFETY`` /
``ERROR`` (and a caller-side ``timeout``) all block.

There is **no CI-only path**, **no idempotency key**, and **no synchronous
shortcut** — a retried ``POST`` is simply another run. ``GET /runs/{id}`` serves
the run's aggregate **verdict resource** (its ``run_id``), and the per-CheckRun
detail (a CheckRun id) with stage outcomes on the canonical spine and the derived
connectable/functional view.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import Actor, current_actor
from app.core.enums import RequestSource
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
    actor: Actor = Depends(current_actor),
) -> RunRequestOut:
    """Start a run and return its ``run_id`` (ADR-0005 §2). Async: the run is
    dispatched by the in-app engine; the caller polls ``GET /runs/{run_id}``.

    A named-but-unregistered target is a hard error (``400``), never a silent
    pass (ADR-0005 §3). The request source and requester come from the caller's
    identity, not the body: a machine caller is never ``UI_MANUAL`` and can
    never carry the GUI-only safety override (ADR-0004/0010)."""
    if not actor.is_human:
        if payload.override is not None:
            raise HTTPException(403, "the safety override is GUI-only; a machine identity can't carry one")
        if payload.request_source == RequestSource.UI_MANUAL:
            payload = payload.model_copy(update={"request_source": RequestSource.API})
    if actor.verified:
        payload = payload.model_copy(update={"requested_by": actor.handle})
    try:
        request = run_service.submit_request(session, payload)
    except run_service.UnknownTargetError as exc:
        raise HTTPException(400, str(exc))
    record_audit(session, actor=payload.requested_by, action="run.trigger", entity_type="run_request",
                 entity_id=request.id, request_source=payload.request_source.value,
                 summary=f"Submitted run request (depth={payload.depth.value})",
                 after=payload.model_dump(mode="json"))
    # Nudge the engine to dispatch promptly; the poller also drains PENDING/QUEUED
    # requests, so a missed nudge is still processed (and a wedged lease swept).
    background.add_task(run_service.process_request, request.id, "bg-nudge")
    session.refresh(request)
    return RunRequestOut(
        id=request.id, run_id=request.id, status=request.status.value,
        phase=run_service.request_phase(request).value, depth=request.depth,
        requested_by=request.requested_by, created_at=request.created_at, run_ids=[],
    )


@router.get("/requests")
def list_requests(session: Session = Depends(get_session), limit: int = 50) -> list[dict]:
    return [r.model_dump(mode="json", exclude={"claim_token"}) for r in session.exec(
        select(RunRequest).order_by(RunRequest.created_at.desc()).limit(limit)  # type: ignore[union-attr]
    ).all()]


@router.get("/requests/{request_id}")
def get_request(request_id: str, session: Session = Depends(get_session)) -> dict:
    req = session.get(RunRequest, request_id)
    if not req:
        raise HTTPException(404, "run request not found")
    return run_service.request_resource(session, req)


@router.get("")
def list_runs(
    session: Session = Depends(get_session),
    request_id: Optional[str] = None,
    check_id: Optional[str] = None,
    target_id: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    stmt = select(CheckRun)
    if request_id:
        stmt = stmt.where(CheckRun.request_id == request_id)
    if check_id:
        stmt = stmt.where(CheckRun.check_id == check_id)
    if target_id:
        stmt = stmt.where(CheckRun.target_id == target_id)
    stmt = (stmt.order_by(CheckRun.created_at.desc(), CheckRun.run_id)  # type: ignore[union-attr]
            .offset(max(0, offset)).limit(limit))
    runs = session.exec(stmt).all()
    check_ids = {r.check_id for r in runs}
    checks = {c.id: c for c in session.exec(
        select(Check).where(Check.id.in_(check_ids))  # type: ignore[attr-defined]
    ).all()} if check_ids else {}
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
    """The verdict resource for a Run, or the detail for a single CheckRun.

    A RunRequest id (``req_…``, the id the CI caller polls) returns the aggregate
    verdict resource with ``phase``; a CheckRun id (``run_…``) returns that
    CheckRun's stage outcomes + derived connectable/functional view.
    """
    # Disambiguate by id (RunRequest ids are ``req_…``, CheckRun ids ``run_…``).
    # A ``req_`` id, or any id that isn't a CheckRun, resolves as a RunRequest so
    # an unprefixed id still finds its verdict resource.
    run = None if run_id.startswith("req_") else session.get(CheckRun, run_id)
    if run is None:
        request = session.get(RunRequest, run_id)
        if request:
            return run_service.request_resource(session, request)
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
