"""Run orchestration: submit → expand → execute → aggregate → roll up.

A Run selects Tests/Suites (+ targets) and fans out to one **CheckRun** per
(Check × Target). Each CheckRun executes its Check's operation across the
canonical stage spine via the handler, records a :class:`StageOutcome` per
stage, and derives a verdict:

* the operation is first checked against the **blocklist** — a blocklisted op is
  refused as ``BLOCKED_BY_SAFETY`` and never runs (the R3-never floor, ADR-0017);
* a **deterministic** Check's verdict comes from its compiled predicate over the
  stage evidence — it **gates**;
* an **agentic** Check is **advisory** (``gating=False``) and never a gate input
  (ADR-0008); judge *execution* is Spec 4, so here it records the operation
  outcome as advisory signal.

connectable/functional are **derived** (never stored) — see the API layer.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from sqlmodel import Session, select

from app.core.enums import Evaluator, Expectation, RiskTier, RunStatus, TestStatus
from app.core.ids import utcnow
from app.core.operations import Operation
from app.executors import HandlerContext, get_handler
from app.models import (
    Check,
    CheckRun,
    RunRequest,
    StageOutcome,
    SuiteMember,
    Target,
    Test,
    TestCheck,
)
from app.services.assertion_compiler import evaluate_predicate
from app.services.audit import record_audit
from app.services.roles import find_role, role_within_class
from app.services.safety import SafetyOverride, evaluate_operation_safety
from app.schemas.schemas import RunSubmit

logger = logging.getLogger(__name__)


def _settings():
    from app.core.config import settings

    return settings


def _demo_mode() -> bool:
    try:
        from app.clients.databricks import databricks_available

        return not databricks_available()
    except Exception:  # noqa: BLE001
        return True


def _workspace_client_factory():
    try:
        from app.core.config import get_workspace_client

        return get_workspace_client
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Submission
# --------------------------------------------------------------------------
def submit_request(session: Session, payload: RunSubmit) -> RunRequest:
    """Create a PENDING run request (idempotent on ``idempotency_key``)."""
    if payload.idempotency_key:
        existing = session.exec(
            select(RunRequest).where(RunRequest.idempotency_key == payload.idempotency_key)
        ).first()
        if existing:
            return existing

    settings = _settings()
    request = RunRequest(
        idempotency_key=payload.idempotency_key,
        requested_by=payload.requested_by,
        request_source=payload.request_source,
        suite_ids=payload.suite_ids,
        test_ids=payload.test_ids,
        check_ids=payload.check_ids,
        target_ids=payload.target_ids,
        depth=payload.depth,
        override=payload.override.model_dump() if payload.override else {},
        status=RunStatus.PENDING,
        git_sha=getattr(settings, "GIT_SHA", "unknown"),
        app_version=getattr(settings, "APP_VERSION", "0.0.0"),
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


# --------------------------------------------------------------------------
# Fan-out
# --------------------------------------------------------------------------
class _Selection:
    """One resolved (check, under-test, membership) unit to run."""

    def __init__(self, check: Check, test_id: Optional[str], expectation: Expectation,
                 run_as_role: Optional[str]):
        self.check = check
        self.test_id = test_id
        self.expectation = expectation
        self.run_as_role = run_as_role


def _test_ids_from_request(session: Session, request: RunRequest) -> list[str]:
    ids: set[str] = set(request.test_ids or [])
    if request.suite_ids:
        members = session.exec(
            select(SuiteMember).where(SuiteMember.suite_id.in_(request.suite_ids))  # type: ignore[attr-defined]
        ).all()
        ids.update(m.test_id for m in members)
    return sorted(ids)


def _selections(session: Session, request: RunRequest) -> list[_Selection]:
    """Resolve the request's selection down to concrete Check units."""
    selections: list[_Selection] = []
    seen: set[tuple[Optional[str], str, str]] = set()

    def add(check: Check, test_id: Optional[str], expectation: Expectation, role: Optional[str]):
        key = (test_id, check.id, expectation.value)
        if key in seen:
            return
        seen.add(key)
        selections.append(_Selection(check, test_id, expectation, role))

    test_ids = _test_ids_from_request(session, request)
    if not test_ids and not request.check_ids:
        # No explicit selection => all enabled, Active Tests ("run all",
        # ADR-0017). A Draft never enters "all" — it can't run until accepted.
        test_ids = [t.id for t in session.exec(
            select(Test).where(Test.enabled == True, Test.status == TestStatus.ACTIVE)  # noqa: E712
        ).all()]

    for test_id in test_ids:
        test = session.get(Test, test_id)
        if not test or not test.enabled:
            continue
        if test.status == TestStatus.DRAFT:
            # A Draft never runs, even when named explicitly (user story 9).
            continue
        members = session.exec(
            select(TestCheck).where(TestCheck.test_id == test_id).order_by(TestCheck.sort_order)  # type: ignore[arg-type]
        ).all()
        for m in members:
            check = session.get(Check, m.check_id)
            if not check or not check.enabled:
                continue
            expectation = Expectation.DENY if m.kind == "counterfactual" else check.expectation
            add(check, test_id, expectation, m.run_as_role or test.run_as_role)

    for check_id in request.check_ids or []:
        check = session.get(Check, check_id)
        if check and check.enabled:
            add(check, None, check.expectation, None)

    return selections


def _selected_targets(session: Session, request: RunRequest) -> list[Optional[Target]]:
    if request.target_ids:
        targets = session.exec(
            select(Target).where(Target.id.in_(request.target_ids))  # type: ignore[attr-defined]
        ).all()
    else:
        targets = session.exec(select(Target).where(Target.enabled == True)).all()  # noqa: E712
    return list(targets) if targets else [None]


def expand_request(session: Session, request: RunRequest) -> list[CheckRun]:
    """Materialise PENDING CheckRun rows for a request's selection."""
    selections = _selections(session, request)
    targets = _selected_targets(session, request)

    runs: list[CheckRun] = []
    for sel in selections:
        for target in targets:
            run = CheckRun(
                request_id=request.id,
                test_id=sel.test_id,
                check_id=sel.check.id,
                target_id=target.id if target else None,
                requested_by=request.requested_by,
                request_source=request.request_source,
                depth=request.depth,
                evaluator=sel.check.evaluator,
                expectation=sel.expectation,
                gating=sel.check.evaluator == Evaluator.DETERMINISTIC,
                run_as_role=sel.run_as_role,
                risk_tier=sel.check.risk_tier,
                workspace_name=(target.workspace_name if target else None),
                git_sha=request.git_sha,
                app_version=request.app_version,
                verdict=RunStatus.PENDING,
            )
            session.add(run)
            runs.append(run)
    session.commit()
    for run in runs:
        session.refresh(run)
    return runs


# --------------------------------------------------------------------------
# Safety gate (ADR-0017/0004) — enforced pre-dispatch in the run engine
# --------------------------------------------------------------------------
def _block(session: Session, run: CheckRun, *, message: str, reason: str,
           actor: str, actor_kind: str = "user") -> bool:
    """Record a CheckRun as BLOCKED_BY_SAFETY and audit the block. Returns False."""
    run.verdict = RunStatus.BLOCKED_BY_SAFETY
    run.failure_bucket = "BLOCKED_BY_SAFETY"
    run.failure_stage = "AUTHORIZE"
    run.message = message
    now = utcnow()
    run.started_at = run.started_at or now
    run.finished_at = now
    run.duration_ms = _elapsed_ms(run)
    session.add(run)
    # Audit the block on the *same* transaction as the run write — one commit,
    # so the concurrent run engine doesn't multiply write contention.
    record_audit(
        session, actor=actor, actor_kind=actor_kind, action="safety.block",
        entity_type="check_run", entity_id=run.run_id, summary=reason,
        request_source=run.request_source.value, commit=False,
        after={"check_id": run.check_id, "target_id": run.target_id,
               "run_as_role": run.run_as_role, "risk_tier": run.risk_tier},
    )
    session.commit()
    return False


def _apply_safety_gate(session: Session, run: CheckRun, check: Check,
                       target: Optional[Target], operation: Operation,
                       request: Optional[RunRequest]) -> bool:
    """Run the identity-bounded gate for one CheckRun. Returns True to proceed.

    On any refusal it records BLOCKED_BY_SAFETY (via :func:`_block`) and returns
    False so the caller stops before dispatching the operation.
    """
    actor = run.requested_by or "system"

    # (a) Resolve the run-as-role against the target's registered roles. An
    # unregistered role — or one that out-runs the target's class — is refused
    # before dispatch (user story 22 / ADR-0018), keeping the primary identity
    # bound intact.
    if run.run_as_role:
        if target is None:
            return _block(session, run, actor=actor,
                          message="run-as-role requires a registered target.",
                          reason="run-as-role with no target")
        role = find_role(target.assumable_roles, run.run_as_role)
        if role is None:
            return _block(session, run, actor=actor,
                          message=f"run-as-role '{run.run_as_role}' is not registered on target '{target.name}'.",
                          reason=f"unregistered run-as-role '{run.run_as_role}'")
        if not role_within_class(role, target.safety_class):
            return _block(session, run, actor=actor,
                          message=f"run-as-role '{run.run_as_role}' out-runs target class '{target.safety_class.value}'.",
                          reason=f"over-privileged run-as-role '{run.run_as_role}'")

    # (b)+(c) Blocklist floor and coarse-tier-vs-class cap, with the human-only
    # override as the sole escape.
    confirmed = None
    if check.confirmed_risk_tier:
        try:
            confirmed = RiskTier(check.confirmed_risk_tier)
        except ValueError:
            confirmed = None

    override = SafetyOverride.from_payload(request.override if request else None)
    decision = evaluate_operation_safety(
        operation,
        target.safety_class if target else None,
        confirmed_tier=confirmed,
        override=override,
        request_source=run.request_source,
        target_name=target.name if target else None,
    )
    # Store the tier the gate actually weighed (the effective tier), so the run
    # record reflects what was enforced, not just the check's coarse guess.
    run.risk_tier = decision.effective_tier.value

    if not decision.allowed:
        return _block(session, run, actor=actor, message=decision.reason,
                      reason=decision.reason)

    if decision.overridden:
        # A consumed override is a safety-relevant decision — audit it (ADR-0004 §7).
        # commit=False: the run's own commit at the end of execution flushes it.
        record_audit(
            session, actor=actor, action="safety.override", entity_type="check_run",
            entity_id=run.run_id, summary=decision.reason, commit=False,
            request_source=run.request_source.value, after=decision.evidence,
        )
    return True


def _resolve_identity_token(target: Optional[Target], run_as_role: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Resolve a Run's identity to ``(target, run-as-role?)`` and mint its token.

    Returns ``(access_token, assumed_group)``. Best-effort and non-fatal: in demo
    mode (no workspace binding) or when the target has no identity handle it
    returns ``(None, None)`` — the platform stays exercisable without a workspace.
    When a role is set, the token is minted with ``assume_group`` so the role's
    grants replace the SP's (ADR-0018); otherwise it's the plain SP token
    (ADR-0003). The client caches per ``(identity_handle, assumed_group)`` and
    never persists (ADR-0003 §4).
    """
    if target is None or not target.identity_handle:
        return None, None
    assumed_group = None
    if run_as_role:
        role = find_role(target.assumable_roles, run_as_role)
        assumed_group = role.group_id if role else None
    try:
        from app.clients.databricks import acquire_target_token, databricks_available

        if not databricks_available():
            return None, assumed_group  # demo / no binding — resolution only
        token = acquire_target_token(target.identity_handle, target.host, assumed_group)
        return token.access_token, assumed_group
    except Exception as exc:  # noqa: BLE001 - identity resolution is best-effort here
        logger.info("Per-target token unavailable (demo/degraded): %s", exc)
        return None, assumed_group


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def execute_check_run(run_id: str) -> None:
    """Execute a single CheckRun in its own session (thread-safe)."""
    from app.db.session import session_scope

    with session_scope() as session:
        run = session.get(CheckRun, run_id)
        if run is None:
            return
        check = session.get(Check, run.check_id)
        target = session.get(Target, run.target_id) if run.target_id else None
        if check is None:
            run.verdict = RunStatus.ERROR
            run.message = "Check not found."
            run.finished_at = utcnow()
            session.add(run)
            session.commit()
            return

        operation = Operation.from_dict(check.operation)
        # Read the request (for the override) BEFORE mutating the run — doing all
        # reads up front means the worker doesn't autoflush a pending write and
        # grab the SQLite write-lock early, holding it across the whole handler
        # run (which serializes every concurrent worker past the busy timeout).
        request = session.get(RunRequest, run.request_id) if run.request_id else None

        # Identity-bounded safety gate (ADR-0017/0004), pre-dispatch. Resolve the
        # run-as-role first (an unregistered/over-privileged role is refused
        # before dispatch), then run the gate. A blocked op never reaches the
        # handler and is recorded loudly as BLOCKED_BY_SAFETY.
        if not _apply_safety_gate(session, run, check, target, operation, request):
            return

        run.started_at = utcnow()
        params = dict(operation.params)
        if target and target.tags:
            params.setdefault("_target_tags", target.tags)

        # Resolve identity to (target, run-as-role?) and mint its token — the SP
        # by default, or one that assumes the role's group (ADR-0018). Best-effort:
        # None in demo mode; consumed by handlers to make the real call (Spec 3).
        token, assumed_group = _resolve_identity_token(target, run.run_as_role)

        ctx = HandlerContext(
            run_id=run.run_id,
            operation=operation,
            check=check,
            target=target,
            depth=run.depth,
            params=params,
            workspace_client_factory=_workspace_client_factory(),
            demo_mode=_demo_mode(),
            logger=logger,
            token=token,
            assumed_group=assumed_group,
        )

        handler = get_handler(check.handler_key)
        stage_results = handler.run(ctx)

        for idx, sr in enumerate(stage_results):
            session.add(StageOutcome(
                run_id=run.run_id, stage=sr.stage, sort_order=idx, outcome=sr.outcome,
                message=sr.message, failure_bucket=sr.failure_bucket, raw_excerpt=sr.raw_excerpt,
                evidence=sr.evidence, started_at=sr.started_at, finished_at=sr.finished_at,
                duration_ms=sr.duration_ms,
            ))

        _aggregate(run, check, stage_results)
        _maybe_judge_agentic(run, check)
        session.add(run)
        session.commit()


def _maybe_judge_agentic(run: CheckRun, check: Check) -> None:
    """Run the pinned advisory judge over an agentic Check's evidence (ADR-0008).

    Only fires for an ``agentic`` Check when a Model-Serving endpoint is actually
    configured; otherwise it is a no-op and the operation-outcome advisory signal
    from :func:`_aggregate` stands (demo/local/CI-without-endpoint). The verdict
    it produces is **advisory only** — ``run.gating`` was set False at fan-out, so
    this can never affect the fail-closed roll-up. The judge's cited field(s) are
    recorded on the evidence so the derivation panel can inspect them.
    """
    if run.evaluator != Evaluator.AGENTIC:
        return
    try:
        from app.services.agentic_evaluator import evaluate
        from app.services.assistant import get_model_call

        model_call = get_model_call()
        if model_call is None:
            return
        verdict = evaluate(check.assertion, run.evidence or {}, model_call,
                           expectation=run.expectation)
        evidence = dict(run.evidence or {})
        evidence["_agentic_judge"] = verdict.to_dict()
        run.evidence = evidence
        # Advisory verdict — never gates (gating=False set at fan-out).
        run.verdict = RunStatus.PASS if verdict.passed else RunStatus.FAIL
        if not verdict.passed and not run.failure_bucket:
            run.failure_stage = run.failure_stage or "VERIFY"
            run.failure_bucket = run.failure_bucket or "ADVISORY_JUDGE_FAIL"
    except Exception as exc:  # noqa: BLE001 - advisory; a judge failure never breaks a run
        logger.info("Agentic judge skipped (advisory): %s", exc)


def _elapsed_ms(run: CheckRun) -> Optional[int]:
    if run.started_at and run.finished_at:
        return int((run.finished_at - run.started_at).total_seconds() * 1000)
    return None


def _aggregate(run: CheckRun, check: Check, stage_results: list) -> None:
    """Compute the CheckRun verdict + failure classification from stage outcomes."""
    evidence: dict = {}
    for sr in stage_results:
        if isinstance(sr.evidence, dict):
            evidence.update(sr.evidence)

    first_failed = next((sr for sr in stage_results if sr.failed), None)
    operation_succeeded = first_failed is None

    if run.evaluator == Evaluator.DETERMINISTIC:
        if run.expectation == Expectation.DENY:
            predicate = check.compiled_predicate or {"kind": "denied"}
        else:
            predicate = check.compiled_predicate or None
        passed = evaluate_predicate(predicate, evidence, operation_succeeded)
    else:
        # Agentic: advisory. Judge execution is Spec 4; record the operation
        # outcome as advisory signal (never gates — gating=False set at fan-out).
        passed = operation_succeeded if run.expectation == Expectation.PASS else not operation_succeeded

    run.verdict = RunStatus.PASS if passed else RunStatus.FAIL
    run.evidence = evidence
    run.message = (first_failed.message if first_failed else "All stages passed.")

    if not passed:
        if first_failed is not None:
            run.failure_stage = first_failed.stage
            run.failure_bucket = first_failed.failure_bucket or "UNKNOWN"
        elif run.expectation == Expectation.DENY:
            # Counterfactual that was NOT denied: the operation wrongly succeeded.
            run.failure_stage = "AUTHORIZE"
            run.failure_bucket = "COUNTERFACTUAL_NOT_DENIED"
            run.message = "Counterfactual expected a denial but the operation succeeded."
        else:
            # Operation ran clean but the predicate rejected the evidence.
            run.failure_stage = "VERIFY"
            run.failure_bucket = "UNEXPECTED_RESULT"
            run.message = "Operation succeeded but the assertion predicate was not satisfied."

    run.finished_at = utcnow()
    run.duration_ms = _elapsed_ms(run)


# --------------------------------------------------------------------------
# Request processing
# --------------------------------------------------------------------------
def process_request(request_id: str, worker_id: str = "inline") -> int:
    """Expand and execute a request. Returns the number of CheckRuns executed."""
    from app.db.session import session_scope

    with session_scope() as session:
        request = session.get(RunRequest, request_id)
        if request is None or request.status not in (RunStatus.PENDING, RunStatus.QUEUED):
            return 0
        request.status = RunStatus.RUNNING
        request.locked_by = worker_id
        request.locked_at = utcnow()
        session.add(request)
        session.commit()
        runs = expand_request(session, request)
        run_ids = [r.run_id for r in runs]

    max_workers = max(1, getattr(_settings(), "POLLER_MAX_CONCURRENT", 4))
    if run_ids:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(execute_check_run, run_ids))

    with session_scope() as session:
        request = session.get(RunRequest, request_id)
        if request:
            check_runs = session.exec(
                select(CheckRun).where(CheckRun.request_id == request_id)
            ).all()
            request.status = request_verdict(check_runs)
            session.add(request)
            session.commit()
    return len(run_ids)


# --------------------------------------------------------------------------
# Verdict roll-up (ADR-0005 / ADR-0008): gating vs advisory
# --------------------------------------------------------------------------
def request_verdict(check_runs: list[CheckRun]) -> RunStatus:
    """Roll a request's CheckRuns up to a single fail-closed verdict.

    Only **gating** (deterministic) runs affect the verdict; agentic/advisory
    runs are signal only (ADR-0008). Fail-closed: any non-PASS gating run blocks.
    """
    gating = [r for r in check_runs if r.gating]
    if not gating:
        return RunStatus.PASS
    if any(r.verdict == RunStatus.BLOCKED_BY_SAFETY for r in gating):
        return RunStatus.BLOCKED_BY_SAFETY
    if any(r.verdict == RunStatus.ERROR for r in gating):
        return RunStatus.ERROR
    if any(r.verdict == RunStatus.FAIL for r in gating):
        return RunStatus.FAIL
    if all(r.verdict == RunStatus.PASS for r in gating):
        return RunStatus.PASS
    return RunStatus.RUNNING


def test_rollup(check_runs: list[CheckRun]) -> dict:
    """Per-Test roll-up: {test_id: verdict} over its gating CheckRuns."""
    by_test: dict[Optional[str], list[CheckRun]] = {}
    for r in check_runs:
        by_test.setdefault(r.test_id, []).append(r)
    return {test_id or "(ad-hoc)": request_verdict(runs).value for test_id, runs in by_test.items()}
