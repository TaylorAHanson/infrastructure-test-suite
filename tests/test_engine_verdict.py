"""Fail-closed roll-up: a request with no gating evidence, a cancelled gating run,
or a request that hasn't finished never reads as PASS (ADR-0005/0008)."""
from __future__ import annotations

from app.core.enums import RunStatus
from app.models import Check, CheckRun, RunRequest
from app.services import run_service


def _run(verdict, *, gating=True):
    return CheckRun(check_id="c", test_id="t1", verdict=verdict, gating=gating)


def test_zero_gating_runs_is_an_error_not_a_pass():
    assert run_service.request_verdict([]) == RunStatus.ERROR
    assert run_service.request_verdict([_run(RunStatus.PASS, gating=False)]) == RunStatus.ERROR


def test_every_gating_run_skipped_is_skipped():
    assert run_service.request_verdict([_run(RunStatus.SKIPPED), _run(RunStatus.SKIPPED)]) == RunStatus.SKIPPED
    assert run_service.request_verdict([_run(RunStatus.SKIPPED), _run(RunStatus.PASS)]) == RunStatus.PASS


def test_a_cancelled_gating_run_is_an_error():
    assert run_service.request_verdict([_run(RunStatus.PASS), _run(RunStatus.CANCELLED)]) == RunStatus.ERROR


def test_a_request_that_is_not_terminal_reports_running(session):
    check = Check(display_name="c", operation={"service": "jobs", "method": "list", "verb": "GET"})
    unexpanded = RunRequest(status=RunStatus.PENDING)
    unfinalized = RunRequest(status=RunStatus.RUNNING)
    session.add_all([check, unexpanded, unfinalized])
    session.commit()
    session.add(CheckRun(request_id=unfinalized.id, check_id=check.id, verdict=RunStatus.PASS, gating=True))
    session.commit()

    assert run_service.request_resource(session, unexpanded)["verdict"] == "RUNNING"
    assert run_service.request_resource(session, unfinalized)["verdict"] == "RUNNING"
