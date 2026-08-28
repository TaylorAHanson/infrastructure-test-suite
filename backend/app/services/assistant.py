"""The Assistant harness — one agent, differentiated by tools (ADR-0012).

There is exactly **one** Assistant: one pinned Model-Serving endpoint and one
tool-dispatch layer. Its two capability areas — **Authoring** (write-path,
:mod:`app.services.authoring`) and **Triage/RCA** (read-path,
:mod:`app.services.rca`) — are chosen by the **tools** a turn is routed to, not
by standing up a second agent. Every surface hands the Assistant a **page
context**; :func:`run_turn` routes on it.

Guardrails ride on the tools (ADR-0012 §3): the write-path always runs the
GUI's validator, refuses R3, and is suggest-then-human-confirms (a turn only
*proposes* a Draft; :func:`commit_draft` persists it, and it still doesn't run
until :func:`accept_test`); the read-path is read-only and advisory.

The model is reached through a single injectable ``model_call`` seam
(:func:`get_model_call`) so it can be stubbed in tests and degrade gracefully
when no endpoint is configured.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from sqlmodel import Session, select

from app.core.enums import Expectation, TestStatus
from app.core.ids import utcnow
from app.models import Check, Suite, SuiteMember, Target, Test, TestCheck, WorkedExample
from app.schemas.schemas import AssistantTurnIn, DraftCommitIn
from app.services import authoring, rca, versioning
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

ModelCall = Callable[[list[dict]], str]

_RCA_WORDS = ("why", "regress", "failing", "failed", "broke", "root cause", "started failing")


# ---------------------------------------------------------------------------
# Model-call seam (pinned Model-Serving endpoint; stubbable / degradable)
# ---------------------------------------------------------------------------
def default_model_call(messages: list[dict]) -> str:
    """Call the pinned Model-Serving endpoint (the one grounding harness)."""
    from app.clients import model_serving

    return model_serving.chat(messages)


def get_model_call() -> Optional[ModelCall]:
    """FastAPI dependency: the pinned model caller, or None when unconfigured.

    Returning None (rather than a caller that raises) lets the services report
    ``model_available: False`` and use their deterministic fallback — the
    open-source install works with no endpoint. Tests override this dependency.
    """
    from app.core.config import settings

    if not settings.MODEL_SERVING_ENDPOINT:
        return None
    return default_model_call


# ---------------------------------------------------------------------------
# Turn routing (authoring vs RCA), page-aware
# ---------------------------------------------------------------------------
def _resolve_mode(payload: AssistantTurnIn) -> str:
    if payload.mode in ("authoring", "rca"):
        return payload.mode
    if payload.page_context.finding:
        return "rca"
    if any(w in (payload.turn or "").lower() for w in _RCA_WORDS):
        return "rca"
    return "authoring"


def run_turn(session: Session, payload: AssistantTurnIn,
             model_call: Optional[ModelCall] = None) -> dict:
    """Route one Assistant turn to the authoring or RCA capability (ADR-0012)."""
    mode = _resolve_mode(payload)
    ctx = payload.page_context

    if mode == "rca":
        finding = ctx.finding or {}
        check_id = finding.get("check_id")
        if not check_id:
            return {"mode": "rca", "page": ctx.page,
                    "clarifying_question": "Which finding should I look at? "
                                           "Open a failing check to give me its context."}
        suggestion = rca.suggest(
            session, check_id=check_id,
            target_id=finding.get("target_id") or ctx.target_id,
            turn=payload.turn, model_call=model_call)
        return {"mode": "rca", "page": ctx.page, "rca": suggestion.to_dict()}

    # Authoring: ground on the page's target (if any) and draft a proposal.
    target = _page_target(session, ctx)
    proposal = authoring.draft_from_intent(session, payload.turn, target=target,
                                           model_call=model_call)
    return {"mode": "authoring", "page": ctx.page, "draft": proposal.to_dict()}


def _page_target(session: Session, ctx) -> Optional[Target]:
    tid = ctx.target_id or (ctx.target_ids[0] if ctx.target_ids else None)
    return session.get(Target, tid) if tid else None


# ---------------------------------------------------------------------------
# Write-path persistence (suggest-then-human-confirms → Draft → Active)
# ---------------------------------------------------------------------------
def commit_draft(session: Session, payload: DraftCommitIn, actor: str = "assistant") -> Test:
    """Persist a human-confirmed proposal as a **Draft** Test (does not run).

    Creates the confirmed Check(s) and a ``Draft`` Test (origin ``assistant``),
    optionally filing it into a suggested Suite the human accepted. The Test is
    versioned v1 and audited, but stays a Draft — it **never runs** until
    :func:`accept_test` promotes it (user story 9).
    """
    from app.services.authoring import build_check_fields
    from app.schemas.schemas import CheckCreate, OperationIn

    check_rows: list[Check] = []
    for spec in payload.checks:
        fields = build_check_fields(CheckCreate(
            display_name=spec.assertion[:80] or "Check",
            operation=OperationIn(**spec.operation.model_dump()),
            handler_key=spec.handler_key,
            assertion=spec.assertion,
        ))  # same validator; raises OperationBlocked on R3
        check = Check(**fields)
        session.add(check)
        check_rows.append(check)
    session.commit()
    for c in check_rows:
        session.refresh(c)

    test = Test(name=payload.name, description=payload.description, tags=payload.tags,
                run_as_role=payload.run_as_role, status=TestStatus.DRAFT,
                origin="assistant", derivation=payload.derivation)
    session.add(test)
    session.commit()
    session.refresh(test)

    for i, (spec, check) in enumerate(zip(payload.checks, check_rows)):
        counterpart = None
        if spec.counterpart_index is not None and 0 <= spec.counterpart_index < len(check_rows):
            counterpart = check_rows[spec.counterpart_index].id
        session.add(TestCheck(test_id=test.id, check_id=check.id, sort_order=i,
                              kind=spec.kind, run_as_role=spec.run_as_role,
                              counterpart_check_id=counterpart))
    if payload.suite_id and session.get(Suite, payload.suite_id):
        _append_to_suite(session, payload.suite_id, test.id)
    session.commit()
    session.refresh(test)

    out = _test_snapshot(session, test)
    versioning.snapshot(session, "test", test.id, test.version, out, actor)
    record_audit(session, actor=actor, action="test.create", entity_type="test",
                 entity_id=test.id, summary=f"Assistant drafted test {test.name}", after=out)
    return test


def accept_test(session: Session, test_id: str, actor: str = "api-user") -> Test:
    """Promote a Draft Test to Active (explicit human accept, ADR-0006 §8).

    A no-op-safe: accepting an already-Active Test just returns it. Bumps the
    version + snapshots + audits so the promotion is on the version history.
    """
    test = session.get(Test, test_id)
    if test is None:
        raise ValueError("test not found")
    if test.status == TestStatus.ACTIVE:
        return test
    before = _test_snapshot(session, test)
    test.status = TestStatus.ACTIVE
    test.version += 1
    test.updated_at = utcnow()
    session.add(test)
    session.commit()
    session.refresh(test)
    out = _test_snapshot(session, test)
    versioning.snapshot(session, "test", test.id, test.version, out, actor)
    record_audit(session, actor=actor, action="test.accept", entity_type="test",
                 entity_id=test.id, summary=f"Accepted draft {test.name} → Active",
                 before=before, after=out)
    return test


def promote_example(session: Session, test_id: str, actor: str = "api-user",
                    title: str = "") -> WorkedExample:
    """Promote an accepted Test into the worked-example library (ADR-0006 §9).

    Curated growth of the library, suggest-then-confirm: the human triggers this
    for a good accepted Test. The Test's first Check seeds the example's
    operation + assertion; provenance is kept via ``source_test_id``.
    """
    test = session.get(Test, test_id)
    if test is None:
        raise ValueError("test not found")
    members = session.exec(
        select(TestCheck).where(TestCheck.test_id == test_id).order_by(TestCheck.sort_order)  # type: ignore[arg-type]
    ).all()
    primary = next((session.get(Check, m.check_id) for m in members
                    if m.kind == "assertion"), None)
    cf_member = next((m for m in members if m.kind == "counterfactual"), None)
    counterfactual: dict = {}
    if cf_member:
        cf_check = session.get(Check, cf_member.check_id)
        if cf_check:
            counterfactual = {"assertion": cf_check.assertion, "operation": cf_check.operation}

    example = WorkedExample(
        intent=test.description or test.name,
        title=title or test.name,
        description=test.description,
        operation=(primary.operation if primary else {}),
        handler_key=(primary.handler_key if primary else "databricks_sdk"),
        assertion=(primary.assertion if primary else ""),
        expectation=(primary.expectation if primary else Expectation.PASS),
        counterfactual=counterfactual,
        tags=test.tags, curated=True, source_test_id=test.id,
    )
    session.add(example)
    session.commit()
    session.refresh(example)
    record_audit(session, actor=actor, action="example.promote", entity_type="worked_example",
                 entity_id=example.id, summary=f"Promoted {test.name} to the example library",
                 after={"intent": example.intent, "source_test_id": test.id})
    return example


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _append_to_suite(session: Session, suite_id: str, test_id: str) -> None:
    existing = session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite_id)
    ).all()
    if any(m.test_id == test_id for m in existing):
        return
    session.add(SuiteMember(suite_id=suite_id, test_id=test_id, sort_order=len(existing)))


def _test_snapshot(session: Session, test: Test) -> dict:
    rows = session.exec(
        select(TestCheck).where(TestCheck.test_id == test.id).order_by(TestCheck.sort_order)  # type: ignore[arg-type]
    ).all()
    return {
        **test.model_dump(mode="json"),
        "members": [{"check_id": m.check_id, "kind": m.kind, "run_as_role": m.run_as_role,
                     "counterpart_check_id": m.counterpart_check_id, "sort_order": m.sort_order}
                    for m in rows],
    }
