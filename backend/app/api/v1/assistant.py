"""The Assistant endpoint — one page-aware agent (ADR-0012).

A single surface for both capability areas: a turn carries a **page context** and
returns an authoring **draft** or an RCA **suggestion** depending on how the
harness routes it (:mod:`app.services.assistant`). Plus the write-path
persistence (confirm a Draft, promote an accepted Test to the example library)
and the advisory agentic judge (ADR-0008). Guardrails ride on the services, not
here — this layer is thin HTTP glue.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db.session import get_session
from app.handlers.registry import OperationBlocked
from app.models import Suite, SuiteMember, Test, WorkedExample
from app.schemas.schemas import (
    AgenticEvalIn,
    AssistantTurnIn,
    DraftCommitIn,
    ExamplePromoteIn,
    RcaRequestIn,
    RunProposalIn,
    RunRequestOut,
    SuiteComposeConfirmIn,
)
from app.core.enums import RequestSource
from app.schemas.schemas import RunSubmit
from app.services import agentic_evaluator, assistant, rca, run_service
from app.services import suite_safety as _suite_safety
from app.services.assistant import get_model_call
from app.services.audit import record_audit
from app.services import pinned_versions, versioning
from app.core.ids import utcnow

router = APIRouter()


@router.post("/turn")
def assistant_turn(payload: AssistantTurnIn,
                   session: Session = Depends(get_session),
                   model_call=Depends(get_model_call)) -> dict:
    """Route one turn to authoring or RCA, grounded in the page context."""
    return assistant.run_turn(session, payload, model_call=model_call)


@router.post("/draft", status_code=201)
def commit_draft(payload: DraftCommitIn,
                 session: Session = Depends(get_session),
                 actor: str = Query("assistant")) -> dict:
    """Persist a human-confirmed proposal as a Draft Test (does not run)."""
    try:
        test = assistant.commit_draft(session, payload, actor=actor)
    except OperationBlocked as exc:
        raise HTTPException(422, str(exc))
    return pinned_versions.test_read(session, test)


@router.post("/rca")
def rca_suggest(payload: RcaRequestIn,
                session: Session = Depends(get_session),
                model_call=Depends(get_model_call)) -> dict:
    """Suggest (never assert) a cause + lane for a finding — read-only, advisory."""
    return rca.suggest(session, check_id=payload.check_id, target_id=payload.target_id,
                       turn=payload.turn, model_call=model_call).to_dict()


@router.post("/evaluate-agentic")
def evaluate_agentic(payload: AgenticEvalIn,
                     model_call=Depends(get_model_call)) -> dict:
    """Judge a plain-text assertion over evidence — advisory, must cite a field."""
    return agentic_evaluator.evaluate(payload.assertion, payload.evidence, model_call,
                                      expectation=payload.expectation).to_dict()


@router.get("/examples")
def list_examples(session: Session = Depends(get_session),
                  q: Optional[str] = None) -> list[dict]:
    """The worked-example library the Assistant grounds on (ADR-0006 §9)."""
    if q:
        from app.services.assistant_tools import search_examples

        return search_examples(session, q, limit=50)
    return [{
        "id": ex.id, "intent": ex.intent, "title": ex.title, "operation": ex.operation,
        "assertion": ex.assertion, "expectation": ex.expectation.value, "tags": ex.tags,
        "curated": ex.curated, "source_test_id": ex.source_test_id,
    } for ex in session.exec(select(WorkedExample)).all()]


@router.post("/run", response_model=RunRequestOut, status_code=202)
def submit_run_from_proposal(
    payload: RunProposalIn,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    actor: str = Query("assistant"),
) -> RunRequestOut:
    """Submit a human-confirmed run proposal through the shared run engine.

    The confirmation half of suggest-then-human-confirms for run submission
    (issue #64 AC #3): routes through ``run_service.submit_request()`` — the
    exact same path as ``POST /runs`` — so safety-class checks and TargetLease
    enforcement are identical (AC #2). No new authorization code path.
    """
    run_payload = RunSubmit(
        suite_ids=payload.suite_ids,
        test_ids=payload.test_ids,
        target_ids=payload.target_ids,
        depth=payload.depth,
        requested_by=actor,
        request_source=RequestSource.API,
    )
    try:
        request = run_service.submit_request(session, run_payload)
    except run_service.UnknownTargetError as exc:
        raise HTTPException(400, str(exc))
    background.add_task(run_service.process_request, request.id, "assistant-bg")
    session.refresh(request)
    return RunRequestOut(
        id=request.id, run_id=request.id, status=request.status.value,
        phase=run_service.request_phase(request).value, depth=request.depth,
        requested_by=request.requested_by, created_at=request.created_at, run_ids=[],
    )


@router.post("/examples/promote", status_code=201)
def promote_example(payload: ExamplePromoteIn,
                    session: Session = Depends(get_session),
                    actor: str = Query("api-user")) -> dict:
    """Promote an accepted Test into the example library (suggest-then-confirm)."""
    try:
        example = assistant.promote_example(session, payload.test_id, actor=actor,
                                            title=payload.title)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"id": example.id, "intent": example.intent, "title": example.title,
            "source_test_id": example.source_test_id}


# ---------------------------------------------------------------------------
# Suite composition confirm (issue #63, spec #56 user stories 8-11)
# ---------------------------------------------------------------------------
def _suite_snapshot(session: Session, suite: Suite) -> dict:
    members = session.exec(
        select(SuiteMember)
        .where(SuiteMember.suite_id == suite.id)  # type: ignore[arg-type]
        .order_by(SuiteMember.sort_order)  # type: ignore[arg-type]
    ).all()
    return {**suite.model_dump(mode="json"), "test_ids": [m.test_id for m in members]}


def _replace_members(session: Session, suite_id: str, test_ids: list[str]) -> None:
    for m in session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite_id)  # type: ignore[arg-type]
    ).all():
        session.delete(m)
    for i, tid in enumerate(test_ids):
        session.add(SuiteMember(suite_id=suite_id, test_id=tid, sort_order=i))


def _confirm_create_suite(session: Session, payload: SuiteComposeConfirmIn, actor: str) -> dict:
    """Apply a human-confirmed Suite-creation proposal (issue #66) — the missing
    "start from nothing" counterpart to `confirm_suite_compose`'s existing actions.
    """
    if not payload.name:
        raise HTTPException(422, "name required for action=create")
    test_ids = list(payload.test_ids or [])
    for tid in test_ids:
        if not session.get(Test, tid):
            raise HTTPException(404, f"test {tid} not found")
    deterministic_only = bool(payload.deterministic_only)

    draft_suite = Suite(name=payload.name, deterministic_only=deterministic_only)
    violation = _suite_safety.check_deterministic_violation(
        session, draft_suite, test_ids, proposed_deterministic_only=deterministic_only
    )
    if violation:
        raise HTTPException(422, violation)

    suite = Suite(name=payload.name, description=payload.description or "",
                  tags=list(payload.tags or []), deterministic_only=deterministic_only)
    session.add(suite)
    session.commit()
    session.refresh(suite)
    _replace_members(session, suite.id, test_ids)
    session.commit()
    session.refresh(suite)

    out = _suite_snapshot(session, suite)
    versioning.snapshot(session, "suite", suite.id, suite.version, out, actor)
    record_audit(session, actor=actor, action="suite.create", entity_type="suite",
                 entity_id=suite.id, summary=f"Assistant created suite {suite.name}",
                 before=None, after=out)
    return out


@router.post("/suite-compose/confirm")
def confirm_suite_compose(
    payload: SuiteComposeConfirmIn,
    session: Session = Depends(get_session),
    actor: str = Query("assistant"),
) -> dict:
    """Apply a human-confirmed suite creation/composition proposal (suggest-then-confirm).

    Re-validates the deterministic-only invariant before applying so the safety
    gate cannot be bypassed even if the proposal was generated without the tool.
    """
    if payload.action == "create":
        return _confirm_create_suite(session, payload, actor)

    suite = session.get(Suite, payload.suite_id)
    if not suite:
        raise HTTPException(404, "suite not found")

    existing = session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == payload.suite_id)  # type: ignore[arg-type]
    ).all()
    current_ids = [m.test_id for m in existing]
    new_test_ids = list(current_ids)
    new_det_only = suite.deterministic_only

    if payload.action == "add_test":
        if not payload.test_id:
            raise HTTPException(422, "test_id required for add_test")
        if not session.get(Test, payload.test_id):
            raise HTTPException(404, f"test {payload.test_id} not found")
        if payload.test_id not in new_test_ids:
            new_test_ids = current_ids + [payload.test_id]
    elif payload.action == "remove_test":
        if not payload.test_id:
            raise HTTPException(422, "test_id required for remove_test")
        new_test_ids = [t for t in current_ids if t != payload.test_id]
    elif payload.action == "reorder":
        if not payload.test_ids:
            raise HTTPException(422, "test_ids required for reorder")
        new_test_ids = payload.test_ids
    elif payload.action == "toggle_det_only":
        if payload.deterministic_only is None:
            raise HTTPException(422, "deterministic_only required for toggle_det_only")
        new_det_only = payload.deterministic_only
    else:
        raise HTTPException(422, f"unknown action {payload.action!r}")

    # Re-validate the deterministic-only invariant (safety gate re-runs).
    violation = _suite_safety.check_deterministic_violation(
        session, suite, new_test_ids, proposed_deterministic_only=new_det_only
    )
    if violation:
        raise HTTPException(422, violation)

    before_out = _suite_snapshot(session, suite)
    _replace_members(session, payload.suite_id, new_test_ids)
    if payload.action == "toggle_det_only":
        suite.deterministic_only = new_det_only
    suite.version += 1
    suite.updated_at = utcnow()
    session.add(suite)
    session.commit()
    session.refresh(suite)
    out = _suite_snapshot(session, suite)
    versioning.snapshot(session, "suite", suite.id, suite.version, out, actor)
    record_audit(session, actor=actor, action="suite.update", entity_type="suite",
                 entity_id=suite.id,
                 summary=f"Assistant composed suite {suite.name} (action={payload.action})",
                 before=before_out, after=out)
    return out
