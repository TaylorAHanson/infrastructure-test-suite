"""Run orchestration: submission, fan-out, execution, aggregation.

Design principles honoured here:

* **Idempotent submission** - re-submitting the same idempotency key returns the
  existing request, never a duplicate.
* **Metadata-driven fan-out** - a request's selection (suites/tests x targets/
  catalogs) expands into one :class:`TestRun` per (definition, target).
* **Async-friendly** - submission returns immediately (PENDING); the poller (or
  a Databricks Job) does the work. The web process orchestrates, it does not
  block on heavy validation.
* **Separation of connectable vs functional** at aggregation time.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from sqlmodel import Session, select

from app.clients.databricks import databricks_available, get_workspace_context
from app.core.config import get_workspace_client, settings
from app.core.enums import (
    CONNECTIVITY_ROOT_CAUSES,
    ConnectivityOutcome,
    ExecutionMode,
    ProbeOutcome,
    RequestSource,
    RootCauseBucket,
    RunStatus,
    StepKind,
)
from app.core.ids import utcnow
from app.executors import ProbeContext, get_executor
from app.models import RunRequest, SuiteMember, TestDefinition, TestRun, TestRunStep, TestTarget
from app.schemas.schemas import RunSubmit

logger = logging.getLogger(__name__)


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
            logger.info("Idempotent hit for key %s -> request %s", payload.idempotency_key, existing.id)
            return existing

    ctx = get_workspace_context()
    request = RunRequest(
        idempotency_key=payload.idempotency_key,
        requested_by=payload.requested_by,
        request_source=payload.request_source,
        suite_ids=payload.suite_ids,
        test_definition_ids=payload.test_definition_ids,
        target_ids=payload.target_ids,
        catalogs=payload.catalogs,
        execution_mode=payload.execution_mode,
        status=RunStatus.PENDING,
        git_sha=settings.GIT_SHA,
        app_version=settings.APP_VERSION,
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


# --------------------------------------------------------------------------
# Fan-out
# --------------------------------------------------------------------------
def _selected_definition_ids(session: Session, request: RunRequest) -> list[str]:
    ids: set[str] = set(request.test_definition_ids or [])
    if request.suite_ids:
        members = session.exec(
            select(SuiteMember).where(SuiteMember.suite_id.in_(request.suite_ids))  # type: ignore[attr-defined]
        ).all()
        ids.update(m.test_definition_id for m in members)
    if not ids:
        # No explicit selection => all enabled definitions ("full suite").
        defs = session.exec(select(TestDefinition).where(TestDefinition.enabled == True)).all()  # noqa: E712
        ids.update(d.id for d in defs)
    return sorted(ids)


def _selected_targets(session: Session, request: RunRequest) -> list[Optional[TestTarget]]:
    if request.target_ids:
        targets = session.exec(
            select(TestTarget).where(TestTarget.id.in_(request.target_ids))  # type: ignore[attr-defined]
        ).all()
    elif request.catalogs:
        targets = session.exec(
            select(TestTarget).where(TestTarget.catalog.in_(request.catalogs))  # type: ignore[attr-defined]
        ).all()
    else:
        targets = session.exec(select(TestTarget).where(TestTarget.enabled == True)).all()  # noqa: E712
    # Allow tests with no target (e.g. workspace-global probes).
    return list(targets) if targets else [None]


def expand_request(session: Session, request: RunRequest) -> list[TestRun]:
    """Materialise PENDING TestRun rows for a request's selection."""
    def_ids = _selected_definition_ids(session, request)
    definitions = session.exec(
        select(TestDefinition).where(TestDefinition.id.in_(def_ids))  # type: ignore[attr-defined]
    ).all()
    targets = _selected_targets(session, request)
    wctx = get_workspace_context()

    runs: list[TestRun] = []
    for definition in definitions:
        for target in targets:
            mode = request.execution_mode
            # Honour each definition's supported modes; downgrade if needed.
            supported = definition.supported_execution_modes or [ExecutionMode.PREFLIGHT.value]
            if mode.value not in supported:
                mode = ExecutionMode(supported[-1])
            run = TestRun(
                request_id=request.id,
                test_definition_id=definition.id,
                target_id=target.id if target else None,
                requested_by=request.requested_by,
                request_source=request.request_source,
                execution_mode=mode,
                workspace_id=(target.workspace_id if target else None) or wctx.workspace_id,
                workspace_name=(target.workspace_name if target else None) or wctx.workspace_name,
                cloud=(target.cloud if target else None) or wctx.cloud,
                account_id=(target.account_id if target else None) or wctx.account_id,
                region=(target.region if target else None) or wctx.region,
                catalog=target.catalog if target else None,
                git_sha=request.git_sha,
                app_version=request.app_version,
                idempotency_key=request.idempotency_key,
                status=RunStatus.PENDING,
            )
            session.add(run)
            runs.append(run)
    session.commit()
    for run in runs:
        session.refresh(run)
    return runs


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def execute_run(run_id: str) -> None:
    """Execute a single TestRun in its own session (thread-safe)."""
    from app.db.session import session_scope

    with session_scope() as session:
        run = session.get(TestRun, run_id)
        if run is None:
            return
        definition = session.get(TestDefinition, run.test_definition_id)
        target = session.get(TestTarget, run.target_id) if run.target_id else None
        if definition is None:
            run.status = RunStatus.ERROR
            run.recommendation = "Test definition not found."
            session.add(run)
            session.commit()
            return

        run.status = RunStatus.RUNNING
        run.started_at = utcnow()
        session.add(run)
        session.commit()

        demo = not databricks_available()
        parameters = dict(definition.default_parameters or {})
        if target and target.config:
            parameters.update(target.config)

        ctx = ProbeContext(
            run_id=run.run_id,
            definition=definition,
            target=target,
            execution_mode=run.execution_mode,
            parameters=parameters,
            workspace_client_factory=get_workspace_client,
            demo_mode=demo,
            logger=logger,
        )

        executor = get_executor(definition.executor_key)
        step_results = executor.run(ctx)

        # Persist steps.
        for idx, sr in enumerate(step_results):
            session.add(
                TestRunStep(
                    run_id=run.run_id,
                    step_kind=sr.step_kind,
                    sort_order=idx,
                    outcome=sr.outcome,
                    connectivity=sr.connectivity,
                    started_at=sr.started_at,
                    finished_at=sr.finished_at,
                    duration_ms=sr.duration_ms,
                    message=sr.message,
                    error_class=sr.error_class,
                    root_cause_bucket=sr.root_cause_bucket,
                    recommendation=sr.recommendation,
                    raw_payload_excerpt=sr.raw_payload_excerpt,
                    evidence=sr.evidence,
                )
            )

        _aggregate_run(run, step_results)
        session.add(run)
        session.commit()
        logger.info(
            "run finished",
            extra={"run_id": run.run_id, "test_id": definition.id, "root_cause": run.root_cause_bucket},
        )


def _aggregate_run(run: TestRun, steps: list) -> None:
    """Compute run-level status, connectivity, and root cause from steps."""
    summary: dict[str, int] = {}
    conn_summary: dict[str, int] = {}
    buckets: list[str] = []

    connectable_steps = [s for s in steps if s.step_kind in
                         (StepKind.PRECHECK, StepKind.AUTH, StepKind.DNS, StepKind.NETWORK)]
    # Connectivity verdict is driven by the network-facing steps; PRECHECK is a
    # config gate, not a connectivity signal (but a failed precheck still blocks).
    network_steps = [s for s in steps if s.step_kind in (StepKind.AUTH, StepKind.DNS, StepKind.NETWORK)]
    functional_steps = [s for s in steps if s.step_kind == StepKind.FUNCTIONAL]

    for s in steps:
        summary[s.outcome.value] = summary.get(s.outcome.value, 0) + 1
        conn_summary[s.connectivity.value] = conn_summary.get(s.connectivity.value, 0) + 1
        if s.root_cause_bucket and s.failed:
            buckets.append(s.root_cause_bucket.value)

    # Connectivity outcome.
    if any(s.failed or s.connectivity == ConnectivityOutcome.NOT_CONNECTABLE for s in connectable_steps):
        run.connectivity_outcome = ConnectivityOutcome.NOT_CONNECTABLE
    elif any(s.connectivity == ConnectivityOutcome.CONNECTABLE for s in network_steps):
        run.connectivity_outcome = ConnectivityOutcome.CONNECTABLE
    else:
        run.connectivity_outcome = ConnectivityOutcome.UNKNOWN

    # Functional probe outcome.
    if functional_steps:
        fs = functional_steps[0]
        run.probe_outcome = fs.outcome
    elif any(s.failed for s in connectable_steps):
        run.probe_outcome = ProbeOutcome.FAIL
    elif all(s.outcome == ProbeOutcome.SKIP for s in steps):
        run.probe_outcome = ProbeOutcome.SKIP
    else:
        run.probe_outcome = ProbeOutcome.PARTIAL

    # Root cause = first failing step (connectable steps take precedence).
    first_failing = next(
        (s for s in connectable_steps if s.failed),
        next((s for s in steps if s.failed), None),
    )
    if first_failing is None:
        # Surface a skip-with-bucket (e.g. capability-gated) as informational.
        first_failing = next((s for s in steps if s.root_cause_bucket), None)
    if first_failing and first_failing.root_cause_bucket:
        run.root_cause_bucket = first_failing.root_cause_bucket
        run.recommendation = first_failing.recommendation or first_failing.message

    # Map to run status.
    po = run.probe_outcome
    if po == ProbeOutcome.PASS:
        run.status = RunStatus.PASSED
    elif po == ProbeOutcome.FAIL:
        run.status = RunStatus.FAILED
    elif po == ProbeOutcome.ERROR:
        run.status = RunStatus.ERROR
    elif po == ProbeOutcome.PARTIAL:
        run.status = RunStatus.PARTIAL
    else:  # SKIP
        run.status = RunStatus.PARTIAL

    run.summary_counts = summary
    run.connectivity_summary = conn_summary
    run.failure_buckets = sorted(set(buckets))
    run.finished_at = utcnow()
    if run.started_at:
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)


# --------------------------------------------------------------------------
# Request processing (used by poller and by manual /process)
# --------------------------------------------------------------------------
def process_request(request_id: str, worker_id: str = "inline") -> int:
    """Expand and execute a request. Returns the number of runs executed."""
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

    # Execute runs in parallel with bounded concurrency.
    max_workers = max(1, settings.POLLER_MAX_CONCURRENT)
    if run_ids:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(execute_run, run_ids))

    with session_scope() as session:
        request = session.get(RunRequest, request_id)
        if request:
            request.status = RunStatus.PASSED  # request-level "completed"
            session.add(request)
            session.commit()
    return len(run_ids)


# --------------------------------------------------------------------------
# Remote-agent signed results
# --------------------------------------------------------------------------
def apply_agent_results(session: Session, submit) -> TestRun:
    """Verify a signed remote-agent result and fold it into the run."""
    from app.executors.builtin.remote_agent import verify_signature

    run = session.get(TestRun, submit.run_id)
    if run is None:
        raise ValueError("run_id not found")

    secret = settings.REMOTE_AGENT_SIGNING_SECRET or ""
    steps_payload = [s.model_dump() for s in submit.steps]
    if secret and not verify_signature(secret, submit.run_id, submit.agent_id, steps_payload, submit.signature):
        raise PermissionError("invalid remote-agent signature")

    class _S:  # lightweight step holder for aggregation
        def __init__(self, d):
            self.step_kind = StepKind(d["step_kind"])
            self.outcome = ProbeOutcome(d["outcome"])
            self.connectivity = ConnectivityOutcome(d.get("connectivity", "UNKNOWN"))
            self.root_cause_bucket = (
                RootCauseBucket(d["root_cause_bucket"]) if d.get("root_cause_bucket") else None
            )
            self.recommendation = d.get("recommendation")
            self.message = d.get("message", "")
            self.error_class = d.get("error_class")
            self.raw_payload_excerpt = d.get("raw_payload_excerpt")
            self.evidence = d.get("evidence", {})
            self.started_at = utcnow()
            self.finished_at = utcnow()
            self.duration_ms = 0

        @property
        def failed(self):
            return self.outcome in (ProbeOutcome.FAIL, ProbeOutcome.ERROR)

    holders = [_S(d) for d in steps_payload]
    for idx, h in enumerate(holders):
        session.add(
            TestRunStep(
                run_id=run.run_id, step_kind=h.step_kind, sort_order=100 + idx,
                outcome=h.outcome, connectivity=h.connectivity, message=h.message,
                error_class=h.error_class, root_cause_bucket=h.root_cause_bucket,
                recommendation=h.recommendation, raw_payload_excerpt=h.raw_payload_excerpt,
                evidence=h.evidence,
            )
        )
    _aggregate_run(run, holders)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run
