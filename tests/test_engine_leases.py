"""Target leases are held only while a target's own work runs, and heartbeat
meanwhile — for a Run and for a script dry run (ADR-0011)."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
from sqlmodel import select

from app import models
from app.core import enums
from app.core.enums import RunStatus, SafetyClass
from app.core.ids import utcnow
from app.db.session import session_scope
from app.models import CheckRun, RunRequest, Target, TargetLease
from app.services import locking, run_service, script_tests

from helpers import target_id as _target


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


@pytest.fixture()
def db(db_path):
    from app.db.session import create_all

    create_all()


@pytest.fixture()
def fast_leases(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LEASE_TTL_SECONDS", 1)
    monkeypatch.setattr(settings, "LEASE_HEARTBEAT_SECONDS", 0.1)
    return settings


def _leases():
    with session_scope() as s:
        return {lease.target_id: lease for lease in s.exec(select(TargetLease)).all()}


def _request_on(targets):
    """A PENDING request with one waiting CheckRun of a script Test per target."""
    with session_scope() as s:
        test, _ = script_tests.create_script_test(s, name="lists jobs", actor="tester",
                                                  status=enums.TestStatus.ACTIVE,
                                                  script='with check("lists jobs"):\n    w.jobs.list()\n')
        (member,) = s.exec(select(models.TestCheck).where(models.TestCheck.test_id == test.id)).all()
        req = RunRequest(status=RunStatus.PENDING)
        s.add_all([req, *targets])
        s.flush()
        s.add_all([CheckRun(request_id=req.id, test_id=test.id, check_id=member.check_id, target_id=t.id,
                            gating=True, verdict=RunStatus.PENDING) for t in targets])
        s.commit()
        return req.id, [t.id for t in targets]


def test_each_mutating_target_is_locked_only_while_its_own_work_runs(db, fast_leases, monkeypatch):
    monkeypatch.setattr(fast_leases, "MAX_RUN_CONCURRENCY", 1)
    req_id, (sb1, sb2, ro) = _request_on([
        Target(name="sb1", safety_class=SafetyClass.SANDBOX),
        Target(name="sb2", safety_class=SafetyClass.SANDBOX),
        Target(name="ro", safety_class=SafetyClass.READ_ONLY)])
    held_during: dict[str, set[str]] = {}
    beats: dict[str, bool] = {}

    def fake_execute(run_ids):
        (run_id,) = run_ids
        with session_scope() as s:
            target_id = s.get(CheckRun, run_id).target_id
        start = _leases().get(target_id)
        time.sleep(0.4)
        held_during[target_id] = set(_leases())
        if start is not None:
            beats[target_id] = _leases()[target_id].heartbeat_at > start.heartbeat_at
        with session_scope() as s:
            run = s.get(CheckRun, run_id)
            run.verdict = RunStatus.PASS
            s.add(run)
            s.commit()

    monkeypatch.setattr(script_tests, "execute_script_group", fake_execute)
    run_service.run_request(req_id)

    assert held_during[sb1] == {sb1} and held_during[sb2] == {sb2}
    assert ro not in held_during[ro]
    assert beats == {sb1: True, sb2: True}
    assert _leases() == {}
    with session_scope() as s:
        assert s.get(RunRequest, req_id).status == RunStatus.PASS


def test_restarting_the_deadline_needs_the_lease(db):
    with session_scope() as s:
        assert locking.acquire(s, "tgt-x", holder="me", max_duration_seconds=1)
        before = s.get(TargetLease, "tgt-x").deadline_at
        assert locking.restart_deadline(s, "tgt-x", "someone-else") is False
        assert locking.restart_deadline(s, "tgt-x", "me", max_duration_seconds=600) is True
        assert s.get(TargetLease, "tgt-x").deadline_at >= before + timedelta(seconds=500)


def test_a_dry_run_keeps_its_lease_alive_past_the_ttl(client, fast_leases, monkeypatch):
    sandbox = _target(client, "sandbox")
    real = script_tests.run_script
    observed = {}

    def slow_run_script(script, ctx):
        time.sleep(1.5)
        with session_scope() as s:
            observed["reclaimed"] = sandbox in locking.sweep(s, now=utcnow())
        observed["holder"] = getattr(_leases().get(sandbox), "holder", None)
        return real(script, ctx)

    monkeypatch.setattr(script_tests, "run_script", slow_run_script)
    r = client.post("/api/v1/scripts/dry-run", json={
        "script": 'with check("ok"):\n    w.current_user.me()\n', "target_id": sandbox, "depth": "quick"})
    assert r.status_code == 200, r.text
    assert observed["reclaimed"] is False
    assert (observed["holder"] or "").startswith("dryrun-")
    assert sandbox not in _leases()


def test_a_dry_run_on_a_busy_target_is_refused(client):
    sandbox = _target(client, "sandbox")
    with session_scope() as s:
        assert locking.acquire(s, sandbox, holder="a-run")
    r = client.post("/api/v1/scripts/dry-run", json={
        "script": 'with check("ok"):\n    pass\n', "target_id": sandbox, "depth": "quick"})
    assert r.status_code == 409
    assert _leases()[sandbox].holder == "a-run"
