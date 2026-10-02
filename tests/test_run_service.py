"""Service-layer seam for verdict aggregation (spec #21, ADR-0005/0008).

Roll-up is matrix-heavy and the gating-vs-advisory rule is load-bearing (an
agentic Check must never flip a CI pass to fail), so it is unit-tested directly
over synthetic CheckRuns — no DB, no HTTP.
"""
from __future__ import annotations

from datetime import timedelta

from app.core.enums import RunStatus
from app.core.ids import utcnow
from app.models import Check, CheckRun, RunRequest
from app.services import run_service


def _run(verdict, *, gating, bucket=None, test_id="t1"):
    return CheckRun(check_id="c", test_id=test_id, verdict=verdict, gating=gating,
                    failure_bucket=bucket)


# ------------------------- gating vs advisory -------------------------
def test_agentic_advisory_never_flips_a_pass_to_fail():
    # Deterministic (gating) PASS + agentic (advisory) FAIL → the request still
    # passes: advisory results are signal, never a gate input (ADR-0008).
    runs = [_run(RunStatus.PASS, gating=True),
            _run(RunStatus.FAIL, gating=False, bucket="UNEXPECTED_RESULT")]
    assert run_service.request_verdict(runs) == RunStatus.PASS


def test_no_gating_runs_is_an_error():
    # Fail-closed: a request that gated nothing proved nothing, so it can't PASS.
    runs = [_run(RunStatus.PASS, gating=False, bucket="JUDGE_UNSURE")]
    assert run_service.request_verdict(runs) == RunStatus.ERROR
    assert run_service.request_verdict([]) == RunStatus.ERROR


def test_a_failed_gating_run_blocks():
    runs = [_run(RunStatus.PASS, gating=True), _run(RunStatus.FAIL, gating=True, bucket="X")]
    assert run_service.request_verdict(runs) == RunStatus.FAIL


def test_blocked_by_safety_dominates_a_plain_fail():
    runs = [_run(RunStatus.FAIL, gating=True), _run(RunStatus.BLOCKED_BY_SAFETY, gating=True)]
    assert run_service.request_verdict(runs) == RunStatus.BLOCKED_BY_SAFETY


def test_error_blocks_fail_closed():
    runs = [_run(RunStatus.PASS, gating=True), _run(RunStatus.ERROR, gating=True)]
    assert run_service.request_verdict(runs) == RunStatus.ERROR


def test_a_non_terminal_gating_run_keeps_the_request_running():
    runs = [_run(RunStatus.PASS, gating=True), _run(RunStatus.QUEUED, gating=True)]
    assert run_service.request_verdict(runs) == RunStatus.RUNNING


# --------------------------- failure summary ---------------------------
def test_failure_summary_separates_gating_from_advisory():
    runs = [_run(RunStatus.FAIL, gating=True, bucket="UNEXPECTED_RESULT"),
            _run(RunStatus.FAIL, gating=True, bucket="UNEXPECTED_RESULT"),
            _run(RunStatus.FAIL, gating=False, bucket="JUDGE_UNSURE")]
    summary = run_service.failure_summary(runs)
    assert summary["gating"] == {"UNEXPECTED_RESULT": 2}
    assert summary["advisory"] == {"JUDGE_UNSURE": 1}


# ------------------------------ per-test roll-up ------------------------------
def test_test_rollup_is_per_test():
    runs = [_run(RunStatus.PASS, gating=True, test_id="a"),
            _run(RunStatus.FAIL, gating=True, test_id="b")]
    rollup = run_service.test_rollup(runs)
    assert rollup == {"a": "PASS", "b": "FAIL"}


# ------------------- max-run-duration termination (ADR-0011 §5) -------------------
def test_reap_wedged_runs_terminates_an_overrunning_run(session):
    # A run whose CheckRun started longer ago than the max run duration is wedged:
    # the reaper records it ERROR (fail-closed) and re-finalizes its request, so it
    # reaches a terminal verdict instead of holding on forever.
    chk = Check(display_name="slow", operation={"service": "jobs", "method": "list", "verb": "GET"})
    req = RunRequest(status=RunStatus.RUNNING)
    session.add(chk)
    session.add(req)
    session.commit()
    session.refresh(chk)
    session.refresh(req)
    run = CheckRun(request_id=req.id, check_id=chk.id, verdict=RunStatus.PENDING, gating=True,
                   started_at=utcnow() - timedelta(days=1))  # far past any max duration
    session.add(run)
    session.commit()
    session.refresh(run)

    assert run_service.reap_wedged_runs() == 1

    session.expire_all()
    reaped = session.get(CheckRun, run.run_id)
    assert reaped.verdict == RunStatus.ERROR
    assert reaped.failure_bucket == "MAX_DURATION_EXCEEDED"
    # The request is re-finalized to a terminal, fail-closed verdict.
    assert session.get(RunRequest, req.id).status == RunStatus.ERROR


def test_reap_leaves_queued_but_unstarted_runs_alone(session):
    # A run merely queued (never started) is not wedged — do not reap it.
    chk = Check(display_name="waiting", operation={"service": "jobs", "method": "list", "verb": "GET"})
    req = RunRequest(status=RunStatus.QUEUED)
    session.add(chk)
    session.add(req)
    session.commit()
    session.refresh(chk)
    session.refresh(req)
    run = CheckRun(request_id=req.id, check_id=chk.id, verdict=RunStatus.QUEUED, gating=True,
                   started_at=None)
    session.add(run)
    session.commit()
    session.refresh(run)

    assert run_service.reap_wedged_runs() == 0
    session.expire_all()
    assert session.get(CheckRun, run.run_id).verdict == RunStatus.QUEUED
