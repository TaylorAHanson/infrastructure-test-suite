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
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Optional

from sqlmodel import Session, select

from app.core.enums import (
    Evaluator,
    Expectation,
    Phase,
    RiskTier,
    RunStatus,
    SafetyClass,
    TERMINAL_STATUSES,
    TestStatus,
)
from app.core.ids import utcnow
from app.core.operations import Operation
from app.handlers import HandlerContext, get_handler
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
from app.services import locking
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
class UnknownTargetError(ValueError):
    """A submit named a target that isn't registered (ADR-0005 §3: a hard error)."""


def _reject_unknown_targets(session: Session, target_ids: list[str]) -> None:
    """An unknown/unregistered target is a **hard error, never a silent pass**.

    ADR-0005 §3: the caller owns "what changed → which target", and a target it
    names that we don't have is a mistake we must surface loudly — not drop
    silently (which would run against fewer targets, or none, and could still
    roll up to PASS).
    """
    if not target_ids:
        return
    known = set(session.exec(
        select(Target.id).where(Target.id.in_(target_ids))  # type: ignore[attr-defined]
    ).all())
    unknown = [tid for tid in target_ids if tid not in known]
    if unknown:
        raise UnknownTargetError(f"unknown/unregistered target(s): {', '.join(unknown)}")


def submit_request(session: Session, payload: RunSubmit) -> RunRequest:
    """Create a PENDING run request.

    There is **no idempotency key** (ADR-0005 §7, as refined by ADR-0011): a
    retried ``POST`` is simply another independent run, and the caller tracks the
    ``run_id`` it holds. A named-but-unregistered target is refused up front
    (:func:`_reject_unknown_targets`, ADR-0005 §3).
    """
    _reject_unknown_targets(session, payload.target_ids)
    settings = _settings()
    request = RunRequest(
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
        event_ref=payload.event_ref,
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
                event_ref=request.event_ref,
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
# Request processing — expand once, dispatch under per-target locks, finalize
# --------------------------------------------------------------------------
def process_request(request_id: str, worker_id: str = "inline") -> int:
    """Expand (once) and dispatch a request; returns CheckRuns executed this pass.

    Safe to call repeatedly: a request left in ``QUEUED`` (waiting on a target
    lease) is re-dispatched on a later call (the poller drains the queue), and
    only its still-pending CheckRuns are executed. Concurrency across the
    fan-out is bounded by the ``MAX_RUN_CONCURRENCY`` setting (ADR-0007 §5), and
    same-target serialization is enforced by a per-target lease (ADR-0011).
    """
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
        # Expand only once — a re-dispatch of a queued request must not duplicate
        # its CheckRuns.
        already = session.exec(
            select(CheckRun).where(CheckRun.request_id == request_id)
        ).first()
        if already is None:
            expand_request(session, request)

    executed = _dispatch_pending(request_id)
    _finalize(request_id)
    return executed


def _dispatch_pending(request_id: str) -> int:
    """Run the request's still-pending CheckRuns, grouped by target.

    ``read-only`` (non-locking) targets run immediately and concurrently. A
    mutating target's CheckRuns run only if this request can acquire that
    target's lease; otherwise they stay pending and the request will re-dispatch
    once the lease frees (ADR-0011). Returns the count executed this pass.
    """
    from app.db.session import session_scope

    with session_scope() as session:
        pending = session.exec(
            select(CheckRun).where(
                CheckRun.request_id == request_id,
                CheckRun.verdict.in_([RunStatus.PENDING, RunStatus.QUEUED]),  # type: ignore[attr-defined]
            )
        ).all()
        groups: dict[Optional[str], list[str]] = {}
        for r in pending:
            groups.setdefault(r.target_id, []).append(r.run_id)
        target_class: dict[Optional[str], Optional[SafetyClass]] = {}
        for tid in groups:
            target = session.get(Target, tid) if tid else None
            target_class[tid] = target.safety_class if target else None

    runnable: list[str] = []
    locked_targets: list[str] = []
    queued: list[str] = []
    for tid, run_ids in groups.items():
        if tid is not None and locking.needs_lock(target_class[tid]):
            with session_scope() as session:
                acquired = locking.acquire(session, tid, holder=request_id)
            if acquired:
                locked_targets.append(tid)
                runnable.extend(run_ids)
            else:
                queued.extend(run_ids)  # target busy — this run queues
        else:
            runnable.extend(run_ids)  # read-only / unbound: never locks

    if queued:
        _mark_queued(queued)

    if not runnable:
        return 0

    stop = threading.Event()
    heart = None
    if locked_targets:
        heart = threading.Thread(
            target=_heartbeat_loop, args=(locked_targets, request_id, stop), daemon=True)
        heart.start()
    try:
        _execute_batch(runnable)
    finally:
        stop.set()
        if heart is not None:
            heart.join(timeout=2)
        for tid in locked_targets:
            with session_scope() as session:
                locking.release(session, tid, holder=request_id)
    return len(runnable)


def _execute_batch(run_ids: list[str]) -> None:
    """Execute CheckRuns concurrently, bounded by the configured concurrency."""
    if not run_ids:
        return
    max_workers = max(1, getattr(_settings(), "MAX_RUN_CONCURRENCY", 10))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(execute_check_run, run_ids))


def _mark_queued(run_ids: list[str]) -> None:
    """Record CheckRuns as QUEUED (a non-terminal phase, never a Verdict)."""
    from app.db.session import session_scope

    with session_scope() as session:
        for rid in run_ids:
            run = session.get(CheckRun, rid)
            if run is not None and run.verdict in (RunStatus.PENDING, RunStatus.QUEUED):
                run.verdict = RunStatus.QUEUED
                session.add(run)
        session.commit()


def _heartbeat_loop(target_ids: list[str], holder: str, stop: threading.Event) -> None:
    """Renew held leases while the run executes so a live long run keeps its lock."""
    from app.db.session import session_scope

    interval = max(1, getattr(_settings(), "LEASE_HEARTBEAT_SECONDS", 20))
    while not stop.wait(interval):
        try:
            with session_scope() as session:
                for tid in target_ids:
                    locking.heartbeat(session, tid, holder=holder)
        except Exception as exc:  # noqa: BLE001 - heartbeat is best-effort
            logger.warning("Lease heartbeat failed for %s: %s", target_ids, exc)


def _finalize(request_id: str) -> None:
    """Roll the request up to a verdict, or back to QUEUED if work still waits."""
    from app.db.session import session_scope

    with session_scope() as session:
        request = session.get(RunRequest, request_id)
        if request is None:
            return
        was_terminal = request.status in TERMINAL_STATUSES
        check_runs = session.exec(
            select(CheckRun).where(CheckRun.request_id == request_id)
        ).all()
        waiting = any(
            r.verdict in (RunStatus.PENDING, RunStatus.QUEUED, RunStatus.RUNNING)
            for r in check_runs
        )
        # A request with CheckRuns still waiting on a target lease is not terminal
        # — it holds at QUEUED until the lease frees (invisible to the CI
        # contract, which just keeps polling — ADR-0011 §3).
        request.status = RunStatus.QUEUED if waiting else request_verdict(check_runs)
        session.add(request)
        session.commit()
        # Did this call complete the run? A request reaches a terminal verdict
        # exactly once (a queued re-dispatch stays non-terminal until the lease
        # frees), so firing on that transition fires the completion webhook once.
        just_completed = not was_terminal and request.status in TERMINAL_STATUSES

    if just_completed:
        # The platform's only push (ADR-0016). Best-effort and non-fatal: the
        # emitter swallows its own errors, but guard here too so a broken webhook
        # can never break run finalization.
        try:
            from app.services.webhook import emit_run_completion

            emit_run_completion(request_id)
        except Exception as exc:  # noqa: BLE001 - the nudge must never break a run
            logger.warning("Run-completion webhook skipped (non-fatal): %s", exc)


def drain_queued(worker_id: str = "drain", limit: int = 100) -> int:
    """Re-dispatch QUEUED requests (oldest first) so freed leases are picked up.

    Called by the poller after :func:`app.services.locking.sweep`. FIFO by
    ``created_at`` gives the per-target queue its ordering (ADR-0011 §2).
    """
    from app.db.session import session_scope

    with session_scope() as session:
        request_ids = [
            r.id
            for r in session.exec(
                select(RunRequest)
                .where(RunRequest.status == RunStatus.QUEUED)
                .order_by(RunRequest.created_at)  # type: ignore[arg-type]
                .limit(limit)
            ).all()
        ]
    for request_id in request_ids:
        process_request(request_id, worker_id=worker_id)
    return len(request_ids)


def reap_wedged_runs() -> int:
    """Terminate runs that overran the server-side max duration (ADR-0011 §5).

    A CheckRun that started executing longer ago than ``MAX_RUN_DURATION_SECONDS``
    and is still non-terminal is treated as **wedged**: it is recorded ``ERROR``
    ("max run duration exceeded") and its request re-finalized, so a wedged run
    reaches a terminal (fail-closed) verdict instead of holding on forever. In-app
    threads can't be force-killed, so this terminates the run at the
    platform-contract level; its lease is separately reclaimed by
    :func:`app.services.locking.sweep` (both keyed on the same deadline), and the
    wedged worker's own eventual ``release`` is a no-op once another run holds the
    target. Runs merely ``queued`` (never started) are not wedged and are left be.
    """
    from app.db.session import session_scope

    cutoff = utcnow() - timedelta(seconds=getattr(_settings(), "MAX_RUN_DURATION_SECONDS", 1800))
    reaped_requests: set[str] = set()
    with session_scope() as session:
        stale = session.exec(
            select(CheckRun).where(
                CheckRun.verdict.in_([RunStatus.PENDING, RunStatus.RUNNING]),  # type: ignore[attr-defined]
                CheckRun.started_at.is_not(None),  # type: ignore[union-attr]
                CheckRun.started_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        for run in stale:
            run.verdict = RunStatus.ERROR
            run.failure_bucket = "MAX_DURATION_EXCEEDED"
            run.failure_stage = run.failure_stage or "OPERATE"
            run.message = "Run exceeded the server-side max duration and was terminated."
            run.finished_at = utcnow()
            run.duration_ms = _elapsed_ms(run)
            session.add(run)
            if run.request_id:
                reaped_requests.add(run.request_id)
        session.commit()
    for request_id in reaped_requests:
        _finalize(request_id)
    return len(reaped_requests)


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


def request_phase(request: RunRequest) -> Phase:
    """The coarse lifecycle phase for the async poll contract (ADR-0005/0011).

    Read straight off the request status: ``queued`` while waiting on a busy
    target's lease (non-terminal, invisible to the CI fail-closed mapping),
    ``terminal`` once a final verdict is available, else ``running``. The caller
    owns its own timeout.
    """
    if request.status == RunStatus.QUEUED:
        return Phase.QUEUED
    if request.status in TERMINAL_STATUSES:
        return Phase.TERMINAL
    return Phase.RUNNING


def test_rollup(check_runs: list[CheckRun]) -> dict:
    """Per-Test roll-up: {test_id: verdict} over its gating CheckRuns."""
    by_test: dict[Optional[str], list[CheckRun]] = {}
    for r in check_runs:
        by_test.setdefault(r.test_id, []).append(r)
    return {test_id or "(ad-hoc)": request_verdict(runs).value for test_id, runs in by_test.items()}


def failure_summary(check_runs: list[CheckRun]) -> dict:
    """Failure-taxonomy summary for the verdict resource (ADR-0005 §5).

    ``gating`` counts failure buckets among deterministic (gating) runs — what
    blocks the pipeline; ``advisory`` counts them among agentic runs — signal
    that never gates (ADR-0008). Buckets are string ids, not a closed enum.
    """
    gating: dict[str, int] = {}
    advisory: dict[str, int] = {}
    for r in check_runs:
        if not r.failure_bucket:
            continue
        target = gating if r.gating else advisory
        target[r.failure_bucket] = target.get(r.failure_bucket, 0) + 1
    return {"gating": gating, "advisory": advisory}


def request_resource(session: Session, request: RunRequest) -> dict:
    """The one shared verdict resource for a Run (ADR-0005 §5).

    Overall ``verdict`` (fail-closed, gating-only), ``phase`` for the async poll
    contract, per-Test roll-up, failure-taxonomy summary, the fan-out CheckRun
    ids, and the derived connectable/functional aggregate. This is what a CI
    caller polls to gate the pipeline and what the GUI/dashboard render.
    """
    from app.core.stages import is_connectivity_stage

    runs = session.exec(select(CheckRun).where(CheckRun.request_id == request.id)).all()
    gating_runs = [r for r in runs if r.gating]
    # A cheap connectable/functional aggregate from where gating runs failed: the
    # connectivity path is broken if any gating run failed at/before AUTHORIZE;
    # the functional path is broken if any failed at OPERATE/VERIFY. (Per-CheckRun
    # detail exposes the exact derived view from stage outcomes.)
    conn_broken = any(r.failure_stage and is_connectivity_stage(r.failure_stage) for r in gating_runs)
    func_broken = any(r.failure_stage and not is_connectivity_stage(r.failure_stage) for r in gating_runs)
    connectable = None if not gating_runs else not conn_broken
    functional = None if not gating_runs else not func_broken
    return {
        **request.model_dump(mode="json"),
        "run_id": request.id,
        "run_ids": [r.run_id for r in runs],
        "phase": request_phase(request).value,
        "verdict": request_verdict(runs).value,
        "test_rollup": test_rollup(runs),
        "failure_summary": failure_summary(runs),
        "connectable": connectable,
        "functional": functional,
    }
