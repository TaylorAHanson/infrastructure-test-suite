"""Run orchestration: submit → expand → execute → roll up.

A Run selects Tests/Suites (+ targets) and fans out to one **CheckRun** per
(check × Target). Every Test is a script: each Test runs its script once per
target (:func:`app.services.script_tests.execute_script_group`), which writes
every check's verdict onto its CheckRun. The script runtime's gate re-checks
each call as it happens — a blocklisted call never runs (the R3-never floor,
ADR-0017) and one above the target's class is ``BLOCKED_BY_SAFETY``.

A check gates unless the script marks it ``advisory=True`` (ADR-0008).
connectable/functional are **derived** (never stored) — see the API layer.
"""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Callable, Optional

from sqlalchemy import and_, or_, update
from sqlmodel import Session, select

from app.core.enums import (
    Depth,
    Expectation,
    Phase,
    RunStatus,
    SafetyClass,
    TERMINAL_STATUSES,
    TestStatus,
)
from app.core.ids import utcnow
from app.models import (
    Check,
    CheckRun,
    RunRequest,
    Suite,
    SuiteMember,
    Target,
    Test,
    TestCheck,
)
from app.services import locking
from app.services.roles import find_role
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


class UnknownSelectionError(UnknownTargetError):
    """A submit named a Suite/Test that doesn't exist or can't run.

    A subclass of :class:`UnknownTargetError` so every submit path that already
    maps an unknown target to a ``400`` maps this the same way.
    """


def _reject_bad_selection(session: Session, payload: RunSubmit) -> None:
    """A selection that names nothing runnable is refused, never run as an empty pass.

    Unknown Suite/Test ids, a named Suite with no Tests, or named Tests that are
    all Draft, disabled or without a script (a Draft never runs until accepted)
    would otherwise expand to fewer CheckRuns than asked for — or none. Empty
    lists still mean "all".
    """
    for label, model, ids in (("suite", Suite, payload.suite_ids), ("test", Test, payload.test_ids)):
        if not ids:
            continue
        known = set(session.exec(select(model.id).where(model.id.in_(ids))).all())  # type: ignore[attr-defined]
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise UnknownSelectionError(f"unknown {label}(s): {', '.join(unknown)}")

    if payload.suite_ids:
        with_tests = set(session.exec(
            select(SuiteMember.suite_id).where(SuiteMember.suite_id.in_(payload.suite_ids))  # type: ignore[attr-defined]
        ).all())
        empty = [s for s in session.exec(select(Suite).where(Suite.id.in_(payload.suite_ids))).all()  # type: ignore[attr-defined]
                 if s.id not in with_tests]
        if empty:
            raise UnknownSelectionError("; ".join(f"suite '{s.name}' has no tests" for s in empty))

    if payload.test_ids:
        tests = session.exec(select(Test).where(Test.id.in_(payload.test_ids))).all()  # type: ignore[attr-defined]
        if not any(_runnable(t) for t in tests):
            raise UnknownSelectionError("; ".join(_why_not_runnable(t) for t in tests))


def _runnable(test: Test) -> bool:
    return bool(test.enabled and test.status != TestStatus.DRAFT and test.script)


def _why_not_runnable(test: Test) -> str:
    if test.status == TestStatus.DRAFT:
        return f"test '{test.name}' is a Draft, and a Draft never runs: accept it (make it Active) first"
    if not test.enabled:
        return f"test '{test.name}' is disabled: enable it first"
    return f"test '{test.name}' has no script to run"


def submit_request(session: Session, payload: RunSubmit) -> RunRequest:
    """Create a PENDING run request.

    There is **no idempotency key** (ADR-0005 §7, as refined by ADR-0011): a
    retried ``POST`` is simply another independent run, and the caller tracks the
    ``run_id`` it holds. A named-but-unregistered target, or a selection naming
    nothing runnable, is refused up front (ADR-0005 §3).
    """
    _reject_unknown_targets(session, payload.target_ids)
    _reject_bad_selection(session, payload)
    settings = _settings()
    request = RunRequest(
        requested_by=payload.requested_by,
        request_source=payload.request_source,
        suite_ids=payload.suite_ids,
        test_ids=payload.test_ids,
        target_ids=payload.target_ids,
        depth=payload.depth,
        override=payload.override.model_dump() if payload.override else {},
        params=dict(payload.params or {}),
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

    def __init__(self, check: Check, test_id: str, expectation: Expectation,
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
    """Resolve the request's selection down to each script Test's checks."""
    from app.services.script_tests import is_script_check

    selections: list[_Selection] = []
    seen: set[tuple[str, str, str]] = set()

    def add(check: Check, test_id: str, expectation: Expectation, role: Optional[str]):
        key = (test_id, check.id, expectation.value)
        if key in seen:
            return
        seen.add(key)
        selections.append(_Selection(check, test_id, expectation, role))

    test_ids = _test_ids_from_request(session, request)
    if not test_ids:
        # No explicit selection => all enabled, Active Tests ("run all",
        # ADR-0017). A Draft never enters "all" — it can't run until accepted.
        test_ids = [t.id for t in session.exec(
            select(Test).where(Test.enabled == True, Test.status == TestStatus.ACTIVE,  # noqa: E712
                               Test.script.is_not(None))  # type: ignore[union-attr]
        ).all()]

    tests = {t.id: t for t in session.exec(
        select(Test).where(Test.id.in_(test_ids))  # type: ignore[attr-defined]
    ).all()} if test_ids else {}
    members_by_test: dict[str, list[TestCheck]] = {}
    if tests:
        for m in session.exec(
            select(TestCheck).where(TestCheck.test_id.in_(list(tests)))  # type: ignore[attr-defined]
            .order_by(TestCheck.sort_order)  # type: ignore[arg-type]
        ).all():
            members_by_test.setdefault(m.test_id, []).append(m)
    check_ids = {m.check_id for ms in members_by_test.values() for m in ms}
    checks = {c.id: c for c in session.exec(
        select(Check).where(Check.id.in_(check_ids))  # type: ignore[attr-defined]
    ).all()} if check_ids else {}

    for test_id in test_ids:
        test = tests.get(test_id)
        if not test or not _runnable(test):
            # A Draft never runs, even when named explicitly (user story 9).
            continue
        for m in members_by_test.get(test_id, []):
            check = checks.get(m.check_id)
            if not is_script_check(check) or not check.enabled:
                continue
            expectation = Expectation.DENY if m.kind == "counterfactual" else check.expectation
            add(check, test_id, expectation, m.run_as_role or test.run_as_role)

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
                gating=not (sel.check.hints or {}).get("advisory"),
                run_as_role=sel.run_as_role,
                risk_tier=sel.check.risk_tier,
                workspace_name=target_display_name(target),
                git_sha=request.git_sha,
                event_ref=request.event_ref,
                app_version=request.app_version,
                verdict=RunStatus.PENDING,
            )
            session.add(run)
            runs.append(run)
    session.commit()
    return runs


def target_display_name(target: Any) -> Optional[str]:
    """What a CheckRun records as its workspace: the workspace name, else the Target's name."""
    if target is None:
        return None
    return target.workspace_name or target.name


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------
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
_WAITING = (RunStatus.PENDING, RunStatus.QUEUED)


def claim_check_runs(session: Session, run_ids: list[str], started_at: datetime) -> list[str]:
    """Take still-waiting CheckRuns to RUNNING and commit; returns the ones this caller won.

    The conditional update is what makes a CheckRun execute once even when two
    workers dispatch it, and the committed ``started_at`` is what the reaper times.
    """
    claimed = []
    for rid in run_ids:
        result = session.exec(  # type: ignore[call-overload]
            update(CheckRun)
            .where(CheckRun.run_id == rid, CheckRun.verdict.in_(_WAITING))  # type: ignore[attr-defined]
            .values(verdict=RunStatus.RUNNING, started_at=started_at)
        )
        if result.rowcount == 1:
            claimed.append(rid)
    session.commit()
    return claimed


def lock_owned_runs(session: Session, run_ids: list[str]) -> set[str]:
    """Lock the CheckRuns this worker still owns (still RUNNING) for its final write.

    The reaper takes a wedged run to ERROR; once it has, the late worker must not
    overwrite it. The no-op update holds the rows' write lock until the caller
    commits, so the reaper can't interleave between this check and the write.
    """
    if not run_ids:
        return set()
    session.exec(  # type: ignore[call-overload]
        update(CheckRun)
        .where(CheckRun.run_id.in_(run_ids), CheckRun.verdict == RunStatus.RUNNING)  # type: ignore[attr-defined]
        .values(verdict=RunStatus.RUNNING)
    )
    return set(session.exec(
        select(CheckRun.run_id).where(
            CheckRun.run_id.in_(run_ids), CheckRun.verdict == RunStatus.RUNNING)  # type: ignore[attr-defined]
    ).all())


# --------------------------------------------------------------------------
# Request processing — expand once, dispatch under per-target locks, finalize
# --------------------------------------------------------------------------
_pools_lock = threading.Lock()
_pools: dict[str, tuple[int, ThreadPoolExecutor]] = {}
_handed_off: set[str] = set()


def _pool(name: str, size: int) -> ThreadPoolExecutor:
    with _pools_lock:
        current = _pools.get(name)
        if current is None or current[0] != size:
            if current is not None:
                current[1].shutdown(wait=False)
            current = (size, ThreadPoolExecutor(max_workers=size, thread_name_prefix=name))
            _pools[name] = current
        return current[1]


def _check_pool() -> ThreadPoolExecutor:
    """The one process-wide pool every unit of check work runs on (ADR-0007 §5)."""
    return _pool("check-run", max(1, getattr(_settings(), "MAX_RUN_CONCURRENCY", 10)))


def process_request(request_id: str, worker_id: str = "inline") -> int:
    """Hand a request to the engine; the POST /runs nudge and the poller call this.

    The request runs on the dispatcher pool, so neither the API threadpool nor
    the poller tick waits on it; this returns 0 once it's handed off. With
    ``RUN_DISPATCH_INLINE`` it runs on the caller's thread instead and returns the
    CheckRuns executed (:func:`run_request`).
    """
    settings = _settings()
    if getattr(settings, "RUN_DISPATCH_INLINE", False):
        return run_request(request_id, worker_id)
    with _pools_lock:
        if request_id in _handed_off:
            return 0
        _handed_off.add(request_id)
    _pool("run-dispatch", max(1, getattr(settings, "RUN_DISPATCH_WORKERS", 16))).submit(
        _dispatched, request_id, worker_id)
    return 0


def _dispatched(request_id: str, worker_id: str) -> None:
    with _pools_lock:
        _handed_off.discard(request_id)
    try:
        run_request(request_id, worker_id)
    except Exception:  # noqa: BLE001 - a failed dispatch must not kill the pool thread
        logger.exception("Dispatch of %s failed", request_id)


def run_request(request_id: str, worker_id: str = "inline") -> int:
    """Claim, expand (once), dispatch and finalize a request on this thread.

    Safe to call repeatedly and concurrently: only the caller that wins the
    claim works the request, a request left in ``QUEUED`` (waiting on a target
    lease) is re-dispatched by a later call (the poller drains the queue), and
    only its still-pending CheckRuns are executed. Returns the CheckRuns
    executed this pass.
    """
    from app.db.session import session_scope

    token = _claim(request_id, worker_id)
    if token is None:
        return 0
    executed = 0
    try:
        with session_scope() as session:
            request = session.get(RunRequest, request_id)
            # Expand only once — a re-dispatch of a queued request must not
            # duplicate its CheckRuns.
            already = session.exec(
                select(CheckRun.run_id).where(CheckRun.request_id == request_id).limit(1)
            ).first()
            if already is None:
                expand_request(session, request)
        executed = _dispatch_pending(request_id, holder=f"{request_id}:{token}")
    finally:
        _finalize(request_id, token)
    return executed


def _claim(request_id: str, worker_id: str) -> Optional[str]:
    """Atomically take a PENDING/QUEUED request to RUNNING; returns the claim token.

    ``None`` means another worker (the POST nudge, the poller, another replica)
    already holds it.
    """
    from app.db.session import session_scope

    token = uuid.uuid4().hex
    with session_scope() as session:
        result = session.exec(  # type: ignore[call-overload]
            update(RunRequest)
            .where(RunRequest.id == request_id,
                   RunRequest.status.in_([RunStatus.PENDING, RunStatus.QUEUED]))  # type: ignore[attr-defined]
            .values(status=RunStatus.RUNNING, locked_by=worker_id, locked_at=utcnow(), claim_token=token)
        )
        session.commit()
    return token if result.rowcount == 1 else None


def _dispatch_pending(request_id: str, holder: str) -> int:
    """Run the request's still-pending CheckRuns on the shared pool, target by target.

    ``read-only`` (non-locking) targets' units run independently and
    concurrently. A mutating target's units run as one job that takes the
    target's lease only when it starts and releases it when that target's work
    is done (:func:`_run_locked_target`); a busy target's CheckRuns queue and the
    request re-dispatches once the lease frees (ADR-0011). Returns the count
    executed this pass.
    """
    from app.db.session import session_scope

    with session_scope() as session:
        request = session.get(RunRequest, request_id)
        pending = session.exec(
            select(CheckRun).where(
                CheckRun.request_id == request_id,
                CheckRun.verdict.in_(_WAITING),  # type: ignore[attr-defined]
            ).order_by(CheckRun.created_at)  # type: ignore[arg-type]
        ).all()
        if request is None or not pending:
            return 0
        created_at = request.created_at
        units = _work_units(list(pending))
        target_ids = [tid for tid in units if tid]
        classes: dict[str, SafetyClass] = dict(session.exec(  # type: ignore[arg-type]
            select(Target.id, Target.safety_class).where(Target.id.in_(target_ids))  # type: ignore[attr-defined]
        ).all()) if target_ids else {}

    pool = _check_pool()
    futures = []
    for tid, target_units in units.items():
        if tid is not None and locking.needs_lock(classes.get(tid)):
            futures.append(pool.submit(_run_locked_target, tid, holder, request_id, created_at, target_units))
        else:
            futures += [pool.submit(_run_unit, ids, fn) for ids, fn in target_units]
    executed = 0
    for future in futures:
        try:
            executed += future.result()
        except Exception:  # noqa: BLE001 - one broken unit must not strand the rest
            logger.exception("A unit of %s failed", request_id)
    return executed


_Unit = tuple[list[str], Callable[[], None]]


def _run_unit(run_ids: list[str], fn: Callable[[], None]) -> int:
    fn()
    return len(run_ids)


def _run_locked_target(target_id: str, holder: str, request_id: str, created_at: datetime,
                       units: list[_Unit]) -> int:
    """One mutating target's share of a request, under that target's lease.

    The lease is held only while this target's work runs (a long multi-target Run
    never holds a target it isn't working on), heartbeats meanwhile, and its
    max-duration clock restarts as each unit begins. The target being busy — or
    an older request already queued for it (per-target FIFO) — queues the work.
    """
    from app.db.session import session_scope

    run_ids = [rid for ids, _ in units for rid in ids]
    with session_scope() as session:
        behind = _older_request_queued(session, target_id, request_id, created_at)
    if behind:
        _mark_queued(run_ids)
        return 0
    executed = 0
    with locking.held(target_id, holder) as acquired:
        if not acquired:
            _mark_queued(run_ids)
            return 0
        for i, (ids, fn) in enumerate(units):
            with session_scope() as session:
                still_held = locking.restart_deadline(session, target_id, holder)
            if not still_held:
                _mark_queued([rid for later, _ in units[i:] for rid in later])
                break
            fn()
            executed += len(ids)
    return executed


def _older_request_queued(session: Session, target_id: str, request_id: str,
                          created_at: datetime) -> bool:
    """Whether an older, unfinished request already has CheckRuns queued on this target."""
    older = or_(RunRequest.created_at < created_at,
                and_(RunRequest.created_at == created_at, RunRequest.id < request_id))
    return session.exec(
        select(CheckRun.run_id)
        .join(RunRequest, CheckRun.request_id == RunRequest.id)  # type: ignore[arg-type]
        .where(CheckRun.target_id == target_id,
               CheckRun.verdict == RunStatus.QUEUED,
               CheckRun.request_id != request_id,
               RunRequest.status.notin_(TERMINAL_STATUSES),  # type: ignore[attr-defined]
               older)
        .limit(1)
    ).first() is not None


def _work_units(runs: list[CheckRun]) -> dict[Optional[str], list[_Unit]]:
    """Per target, in order: one unit per script Test.

    A script Test runs its script once per target and records every check's
    CheckRun from that one execution.
    """
    from app.services.script_tests import execute_script_group

    by_target: dict[Optional[str], list[_Unit]] = {}
    groups: dict[tuple, list[str]] = {}
    for run in runs:
        key = (run.target_id, run.test_id)
        if key not in groups:
            groups[key] = []
            by_target.setdefault(run.target_id, []).append(
                (groups[key], partial(execute_script_group, groups[key])))
        groups[key].append(run.run_id)
    return by_target


def _mark_queued(run_ids: list[str]) -> None:
    """Record CheckRuns as QUEUED (a non-terminal phase, never a Verdict)."""
    from app.db.session import session_scope

    if not run_ids:
        return
    with session_scope() as session:
        session.exec(  # type: ignore[call-overload]
            update(CheckRun)
            .where(CheckRun.run_id.in_(run_ids), CheckRun.verdict.in_(_WAITING))  # type: ignore[attr-defined]
            .values(verdict=RunStatus.QUEUED)
        )
        session.commit()


def _finalize(request_id: str, token: Optional[str]) -> None:
    """Roll the request up to a verdict, or back to QUEUED if work still waits.

    Writes only while ``token`` still owns the claim. ``None`` is the reaper,
    which revokes the claim, so the original worker's late finalize is a no-op. A
    terminal verdict is never rewritten.
    """
    from app.db.session import session_scope

    with session_scope() as session:
        check_runs = session.exec(
            select(CheckRun).where(CheckRun.request_id == request_id)
        ).all()
        waiting = any(r.verdict not in TERMINAL_STATUSES for r in check_runs)
        # A request with CheckRuns still waiting on a target lease is not terminal
        # — it holds at QUEUED until the lease frees (invisible to the CI
        # contract, which just keeps polling — ADR-0011 §3).
        status = RunStatus.QUEUED if waiting else request_verdict(check_runs)
        stmt = update(RunRequest).where(
            RunRequest.id == request_id,
            RunRequest.status.notin_(TERMINAL_STATUSES),  # type: ignore[attr-defined]
        )
        values: dict[str, Any] = {"status": status}
        if token is None:
            values["claim_token"] = None
        else:
            stmt = stmt.where(RunRequest.claim_token == token)
        written = session.exec(stmt.values(**values)).rowcount == 1  # type: ignore[call-overload]
        session.commit()
        # Did this call complete the run? A terminal verdict is written exactly
        # once (the update above never rewrites one), so firing on that write
        # fires the completion webhook once.
        just_completed = written and status in TERMINAL_STATUSES

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
    :func:`app.services.locking.sweep` (both keyed on the same deadline, which
    restarts per unit of work), and the wedged worker can neither overwrite the
    ERROR (:func:`lock_owned_runs`), nor re-finalize the request (its claim is
    revoked), nor free a lease someone else now holds. Runs merely ``queued``
    (never started) are not wedged and are left be.

    A request whose claim is that old with no CheckRun executing (its worker died
    between units) is re-queued the same way, so it can't stall forever — or
    stall the per-target FIFO queue behind it.
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
        now = utcnow()
        for run in stale:
            result = session.exec(  # type: ignore[call-overload]
                update(CheckRun)
                .where(CheckRun.run_id == run.run_id,
                       CheckRun.verdict.in_([RunStatus.PENDING, RunStatus.RUNNING]),  # type: ignore[attr-defined]
                       CheckRun.started_at < cutoff)  # type: ignore[operator]
                .values(verdict=RunStatus.ERROR, failure_bucket="MAX_DURATION_EXCEEDED",
                        failure_stage=run.failure_stage or "OPERATE",
                        message="Run exceeded the server-side max duration and was terminated.",
                        finished_at=now,
                        duration_ms=int((now - run.started_at).total_seconds() * 1000))
            )
            if result.rowcount and run.request_id:
                reaped_requests.add(run.request_id)
        session.commit()

        stale_claims = session.exec(
            select(RunRequest.id).where(
                RunRequest.status == RunStatus.RUNNING,
                RunRequest.locked_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        for request_id in stale_claims:
            executing = session.exec(
                select(CheckRun.run_id).where(
                    CheckRun.request_id == request_id, CheckRun.verdict == RunStatus.RUNNING
                ).limit(1)
            ).first()
            if executing is None:
                reaped_requests.add(request_id)
    for request_id in reaped_requests:
        _finalize(request_id, None)
    return len(reaped_requests)


# --------------------------------------------------------------------------
# Verdict roll-up (ADR-0005 / ADR-0008): gating vs advisory
# --------------------------------------------------------------------------
def request_verdict(check_runs: list[CheckRun]) -> RunStatus:
    """Roll a request's CheckRuns up to a single fail-closed verdict.

    Only **gating** runs affect the verdict; an ``advisory=True`` check's runs
    are signal only (ADR-0008). Fail-closed: any non-PASS gating run blocks,
    and so does having no gating run at all — nothing was proven. RUNNING until
    every gating run is terminal. A SKIPPED run (a mutating script check in a
    quick run) neither passes nor blocks; if every gating run was skipped the
    verdict is SKIPPED. A CANCELLED gating run never passes: it is an ERROR.
    """
    gating = [r for r in check_runs if r.gating]
    if not gating:
        return RunStatus.ERROR
    if any(r.verdict not in TERMINAL_STATUSES for r in gating):
        return RunStatus.RUNNING
    ran = [r for r in gating if r.verdict != RunStatus.SKIPPED]
    if not ran:
        return RunStatus.SKIPPED
    if any(r.verdict == RunStatus.BLOCKED_BY_SAFETY for r in ran):
        return RunStatus.BLOCKED_BY_SAFETY
    if any(r.verdict in (RunStatus.ERROR, RunStatus.CANCELLED) for r in ran):
        return RunStatus.ERROR
    if any(r.verdict == RunStatus.FAIL for r in ran):
        return RunStatus.FAIL
    return RunStatus.PASS


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

    ``gating`` counts failure buckets among gating runs — what blocks the
    pipeline; ``advisory`` counts them among advisory runs — signal that never
    gates (ADR-0008). Buckets are string ids, not a closed enum.
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
    gating_runs = [r for r in runs if r.gating and r.verdict not in (RunStatus.SKIPPED, RunStatus.BLOCKED_BY_SAFETY)]
    # A cheap connectable/functional aggregate from where gating runs failed: the
    # connectivity path is broken if any gating run failed at/before AUTHORIZE;
    # the functional path is broken if any failed at OPERATE/VERIFY. A safety
    # block or a skip never reached the workspace, so it says nothing either way;
    # quick depth never runs a functional stage. (Per-CheckRun detail exposes the
    # exact derived view from stage outcomes.)
    failed = [r for r in gating_runs if r.verdict in (RunStatus.FAIL, RunStatus.ERROR) and r.failure_stage]
    conn_broken = any(is_connectivity_stage(r.failure_stage) for r in failed)
    func_broken = any(not is_connectivity_stage(r.failure_stage) for r in failed)
    connectable = None if not gating_runs else not conn_broken
    functional = None if not gating_runs or request.depth == Depth.QUICK else not func_broken
    verdict = request_verdict(runs) if request.status in TERMINAL_STATUSES else RunStatus.RUNNING
    return {
        **request.model_dump(mode="json", exclude={"claim_token"}),
        "run_id": request.id,
        "run_ids": [r.run_id for r in runs],
        "phase": request_phase(request).value,
        "verdict": verdict.value,
        "test_rollup": test_rollup(runs),
        "failure_summary": failure_summary(runs),
        "connectable": connectable,
        "functional": functional,
    }
