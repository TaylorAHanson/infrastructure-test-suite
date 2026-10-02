"""Request dispatch: one worker per request, one execution per CheckRun, a late
worker that can't overwrite the reaper, per-target FIFO, the process-wide
concurrency cap, a non-blocking hand-off, and bulk loads."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlmodel import select

from app.core.enums import RunStatus, SafetyClass
from app.core.ids import utcnow
from app.db.session import get_engine, session_scope
from app import models
from app.models import Check, CheckRun, RunRequest, StageOutcome, Target
from app.services import locking, run_service

from helpers import target_id as _target
from helpers import test_id as _test

POLICY = "Cluster policy enforcement"


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


@pytest.fixture()
def db(db_path):
    from app.db.session import create_all

    create_all()


def _settings():
    from app.core.config import settings

    return settings


def _seed(n=1, *, target_id=None):
    """A PENDING request with ``n`` waiting CheckRuns of a read-only check."""
    with session_scope() as s:
        if target_id is None:
            target = Target(name="ro", safety_class=SafetyClass.READ_ONLY)
            s.add(target)
            s.flush()
            target_id = target.id
        check = Check(display_name="lists jobs", operation={"service": "jobs", "method": "list", "verb": "GET"})
        req = RunRequest(status=RunStatus.PENDING)
        s.add_all([check, req])
        s.flush()
        runs = [CheckRun(request_id=req.id, check_id=check.id, target_id=target_id, gating=True,
                         verdict=RunStatus.PENDING) for _ in range(n)]
        s.add_all(runs)
        s.commit()
        return req.id, target_id, [r.run_id for r in runs]


def _status(request_id):
    with session_scope() as s:
        return s.get(RunRequest, request_id).status


class _Wrapped:
    """A real handler with a hook that runs before it does."""

    def __init__(self, inner, before):
        self.inner, self.before = inner, before

    def run(self, ctx):
        self.before(ctx)
        return self.inner.run(ctx)


def _hook_handler(monkeypatch, before):
    real = run_service.get_handler
    monkeypatch.setattr(run_service, "get_handler", lambda key: _Wrapped(real(key), before))


# --------------------------- claims (S7) ---------------------------
def test_a_request_is_claimed_by_exactly_one_worker(db):
    req_id, _, _ = _seed()
    barrier = threading.Barrier(6)
    tokens = []

    def claim(i):
        barrier.wait()
        tokens.append(run_service._claim(req_id, f"w{i}"))

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(t is not None for t in tokens) == 1
    assert run_service.run_request(req_id, "late") == 0


def test_a_check_run_executes_once_when_dispatched_twice(db, monkeypatch):
    calls = []
    _hook_handler(monkeypatch, lambda ctx: calls.append(ctx.run_id))
    _, _, (run_id,) = _seed()
    threads = [threading.Thread(target=run_service.execute_check_run, args=(run_id,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [run_id]
    with session_scope() as s:
        stages = [o.stage for o in s.exec(select(StageOutcome).where(StageOutcome.run_id == run_id)).all()]
    assert stages and len(set(stages)) == len(stages)


def test_a_late_worker_cannot_overwrite_the_reapers_error(db, monkeypatch):
    req_id, _, (run_id,) = _seed()

    def reaped_mid_flight(ctx):
        with session_scope() as s:
            run = s.get(CheckRun, run_id)
            run.started_at = utcnow() - timedelta(days=1)
            s.add(run)
            s.commit()
        assert run_service.reap_wedged_runs() == 1

    _hook_handler(monkeypatch, reaped_mid_flight)
    run_service.run_request(req_id)

    with session_scope() as s:
        run = s.get(CheckRun, run_id)
        assert run.verdict == RunStatus.ERROR and run.failure_bucket == "MAX_DURATION_EXCEEDED"
        assert s.exec(select(StageOutcome).where(StageOutcome.run_id == run_id)).all() == []
        assert s.get(RunRequest, req_id).status == RunStatus.ERROR


def test_after_the_reaper_requeues_a_request_the_old_claim_cannot_finalize_it(db):
    req_id, _, (run_id,) = _seed()
    stale = run_service._claim(req_id, "worker-a")
    # worker-a dies between units: its claim ages out with nothing executing.
    with session_scope() as s:
        req = s.get(RunRequest, req_id)
        req.locked_at = utcnow() - timedelta(days=1)
        s.add(req)
        s.commit()
    assert run_service.reap_wedged_runs() == 1
    assert _status(req_id) == RunStatus.QUEUED

    fresh = run_service._claim(req_id, "worker-b")
    assert fresh is not None and fresh != stale
    with session_scope() as s:
        run = s.get(CheckRun, run_id)
        run.verdict = RunStatus.PASS
        s.add(run)
        s.commit()
    run_service._finalize(req_id, stale)
    with session_scope() as s:
        req = s.get(RunRequest, req_id)
        assert req.status == RunStatus.RUNNING and req.claim_token == fresh

    run_service._finalize(req_id, fresh)
    assert _status(req_id) == RunStatus.PASS


# ----------------------- prompt started_at (S8) -----------------------
def test_started_at_is_committed_and_no_connection_is_held_while_the_handler_runs(db, monkeypatch):
    seen = {}

    def inspect(ctx):
        seen["checked_out"] = get_engine().pool.checkedout()
        with session_scope() as s:
            run = s.get(CheckRun, ctx.run_id)
            seen["verdict"], seen["started_at"] = run.verdict, run.started_at

    _hook_handler(monkeypatch, inspect)
    _, _, (run_id,) = _seed()
    run_service.execute_check_run(run_id)
    assert seen["checked_out"] == 0
    assert seen["verdict"] == RunStatus.RUNNING and seen["started_at"] is not None


# ------------------------- per-target FIFO (S11) -------------------------
def test_a_new_submission_does_not_jump_an_older_queued_request(client):
    sandbox, policy = _target(client, "sandbox"), _test(client, POLICY)
    body = {"test_ids": [policy], "target_ids": [sandbox], "depth": "full"}
    with session_scope() as s:
        assert locking.acquire(s, sandbox, holder="someone-else")
    older = client.post("/api/v1/runs", json=body).json()["run_id"]
    assert client.get(f"/api/v1/runs/{older}").json()["phase"] == "queued"
    with session_scope() as s:
        assert locking.release(s, sandbox, holder="someone-else")

    newer = client.post("/api/v1/runs", json=body).json()["run_id"]
    assert client.get(f"/api/v1/runs/{newer}").json()["phase"] == "queued"

    run_service.drain_queued()
    for rid in (older, newer):
        assert client.get(f"/api/v1/runs/{rid}").json()["phase"] == "terminal"
    with session_scope() as s:
        def runs(rid):
            return s.exec(select(CheckRun).where(CheckRun.request_id == rid)).all()
        assert max(r.finished_at for r in runs(older)) <= min(r.started_at for r in runs(newer))


# -------------------- global concurrency + dispatcher --------------------
def test_check_work_shares_one_process_wide_concurrency_cap(db, monkeypatch):
    monkeypatch.setattr(_settings(), "MAX_RUN_CONCURRENCY", 2)
    lock, state = threading.Lock(), {"active": 0, "peak": 0}

    def fake_execute(run_id):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        with session_scope() as s:
            run = s.get(CheckRun, run_id)
            run.verdict = RunStatus.PASS
            s.add(run)
            s.commit()

    monkeypatch.setattr(run_service, "execute_check_run", fake_execute)
    requests = [_seed(4)[0], _seed(4)[0]]
    threads = [threading.Thread(target=run_service.run_request, args=(rid,)) for rid in requests]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["peak"] == 2
    assert [_status(rid) for rid in requests] == [RunStatus.PASS, RunStatus.PASS]


def test_dispatch_hands_off_without_blocking_the_caller(db, monkeypatch):
    monkeypatch.setattr(_settings(), "RUN_DISPATCH_INLINE", False)
    started, release = threading.Event(), threading.Event()

    def slow_run_request(request_id, worker_id="inline"):
        started.set()
        release.wait(5)
        return 0

    monkeypatch.setattr(run_service, "run_request", slow_run_request)
    try:
        t0 = time.monotonic()
        assert run_service.process_request("req-slow", "api") == 0
        assert time.monotonic() - t0 < 1
        assert started.wait(2)
    finally:
        release.set()


def test_a_poller_tick_returns_while_a_run_is_still_going(db, monkeypatch):
    from app.workers import poller

    monkeypatch.setattr(_settings(), "RUN_DISPATCH_INLINE", False)
    monkeypatch.setattr(poller, "_sweep_history", lambda: None)
    req_id, _, _ = _seed()
    started, release = threading.Event(), threading.Event()

    def slow_run_request(request_id, worker_id="inline"):
        started.set()
        release.wait(5)
        return 0

    monkeypatch.setattr(run_service, "run_request", slow_run_request)
    try:
        t0 = time.monotonic()
        poller.tick()
        assert time.monotonic() - t0 < 1
        assert started.wait(2)
    finally:
        release.set()


def test_history_retention_sweeps_at_most_once_per_interval(monkeypatch):
    from app.workers import poller

    calls = []
    monkeypatch.setattr(poller, "_sweep_history", lambda: calls.append(1))
    monkeypatch.setattr(poller, "_last_history_sweep", None)
    monkeypatch.setattr(_settings(), "RETENTION_SWEEP_INTERVAL_SECONDS", 3600)
    assert poller._sweep_history_if_due(now=1000.0) is True
    assert poller._sweep_history_if_due(now=1000.0 + 3599) is False
    assert poller._sweep_history_if_due(now=1000.0 + 3600) is True
    assert len(calls) == 2


# ------------------------------ bulk loads ------------------------------
@contextmanager
def _counting_selects():
    statements: list[str] = []

    def on_execute(conn, cursor, statement, *args):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)


def _test_with_checks(n):
    with session_scope() as s:
        target = Target(name="ro", safety_class=SafetyClass.READ_ONLY)
        test = models.Test(name=f"{n} checks", description="")
        checks = [Check(display_name=f"c{i}", operation={"service": "jobs", "method": "list", "verb": "GET"})
                  for i in range(n)]
        s.add_all([target, test, *checks])
        s.flush()
        s.add_all([models.TestCheck(test_id=test.id, check_id=c.id, sort_order=i) for i, c in enumerate(checks)])
        req = RunRequest(test_ids=[test.id], target_ids=[target.id])
        s.add(req)
        s.commit()
        return req.id


def _expand_and_plan(request_id):
    """SELECTs issued to expand a request, then to plan its work units."""
    with session_scope() as s, _counting_selects() as expand:
        run_service.expand_request(s, s.get(RunRequest, request_id))
    with session_scope() as s:
        runs = s.exec(select(CheckRun).where(CheckRun.request_id == request_id)).all()
        assert runs
        with _counting_selects() as plan:
            run_service._work_units(s, list(runs))
    return len(expand), len(plan)


def test_fan_out_and_planning_load_rows_in_bulk(db):
    assert _expand_and_plan(_test_with_checks(2)) == _expand_and_plan(_test_with_checks(12))


def test_marking_work_queued_is_one_statement(db):
    _, _, run_ids = _seed(5)
    statements = []

    def on_execute(conn, cursor, statement, *args):  # noqa: ANN001
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        run_service._mark_queued(run_ids)
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    assert sum(s.lstrip().upper().startswith("UPDATE") for s in statements) == 1
    with session_scope() as s:
        assert {r.verdict for r in s.exec(select(CheckRun)).all()} == {RunStatus.QUEUED}


# --------------------------- GET /runs offset ---------------------------
def test_check_runs_of_a_request_page_with_offset(client):
    run_id = client.post("/api/v1/runs", json={"target_ids": [_target(client, "read-only")]}).json()["run_id"]
    expected = set(client.get(f"/api/v1/runs/{run_id}").json()["run_ids"])
    assert len(expected) > 4
    seen: list[str] = []
    for offset in range(0, len(expected) + 3, 3):
        page = client.get("/api/v1/runs", params={"request_id": run_id, "limit": 3, "offset": offset}).json()
        assert isinstance(page, list)
        seen += [r["run_id"] for r in page]
    assert len(seen) == len(set(seen)) and set(seen) == expected
