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
from app.executors.registry import OperationBlocked
from app.models import WorkedExample
from app.schemas.schemas import (
    AgenticEvalIn,
    AssistantTurnIn,
    DraftCommitIn,
    ExamplePromoteIn,
    RcaRequestIn,
    RunProposalIn,
    RunRequestOut,
)
from app.core.enums import RequestSource
from app.schemas.schemas import RunSubmit
from app.services import agentic_evaluator, assistant, rca, run_service
from app.services.assistant import get_model_call

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
    return assistant.test_snapshot(session, test)


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
