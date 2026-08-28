"""HTTP-level seam for target locking + queueing (spec #21, ADR-0011).

Drives the observable lock behaviour through the async run contract: a colliding
same-target run enters the non-terminal ``queued`` phase (never refused) and runs
once the lease frees; a ``read-only`` run never locks. The timing-sensitive
lease/heartbeat/max-duration matrix lives in ``test_locking.py``.
"""
from __future__ import annotations

from sqlmodel import select

from app.db.session import session_scope
from app.models import TargetLease
from app.services import locking, run_service

from helpers import target_id as _target
from helpers import test_id as _test


def test_colliding_sandbox_run_queues_then_runs_after_release(client):
    sandbox = _target(client, "sandbox")
    policy = _test(client, "Cluster policy enforcement")

    # Simulate a first run holding the sandbox target's lease.
    with session_scope() as s:
        assert locking.acquire(s, sandbox, holder="req-inflight") is True

    run_id = client.post("/api/v1/runs", json={
        "test_ids": [policy], "target_ids": [sandbox], "depth": "full"}).json()["run_id"]

    # Collision → queued (a non-terminal phase, invisible to the CI mapping), not
    # a refusal and not a pass (ADR-0011 §2/§3).
    resource = client.get(f"/api/v1/runs/{run_id}").json()
    assert resource["phase"] == "queued"
    assert resource["verdict"] != "PASS"

    # Free the lease and drain the queue: the queued run now acquires and runs.
    with session_scope() as s:
        assert locking.release(s, sandbox, holder="req-inflight") is True
    run_service.drain_queued()

    done = client.get(f"/api/v1/runs/{run_id}").json()
    assert done["phase"] == "terminal"
    assert done["verdict"] == "PASS"


def test_read_only_run_never_locks(client):
    read_only = _target(client, "read-only")
    sql = _test(client, "SQL Warehouse validation")

    run_id = client.post("/api/v1/runs", json={
        "test_ids": [sql], "target_ids": [read_only], "depth": "full"}).json()["run_id"]

    resource = client.get(f"/api/v1/runs/{run_id}").json()
    assert resource["phase"] == "terminal"
    assert resource["verdict"] == "PASS"
    # A read-only target has no mutation to interfere → no lease row was ever taken.
    with session_scope() as s:
        assert s.exec(select(TargetLease)).all() == []


def test_sweep_reclaims_a_wedged_lease_and_unblocks_the_queue(client):
    sandbox = _target(client, "sandbox")
    policy = _test(client, "Cluster policy enforcement")

    # A run holds a *valid* lease, so a colliding run queues behind it.
    with session_scope() as s:
        assert locking.acquire(s, sandbox, holder="req-wedged") is True

    run_id = client.post("/api/v1/runs", json={
        "test_ids": [policy], "target_ids": [sandbox], "depth": "full"}).json()["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}").json()["phase"] == "queued"

    # The holder then wedges (stops heartbeating / overruns): age its lease out so
    # the sweeper reclaims it, and draining lets the queued run go (ADR-0011 §5).
    from datetime import timedelta
    from app.core.ids import utcnow
    with session_scope() as s:
        lease = s.get(TargetLease, sandbox)
        lease.expires_at = utcnow() - timedelta(seconds=1)
        s.add(lease)
        s.commit()
        assert sandbox in locking.sweep(s)
    run_service.drain_queued()

    assert client.get(f"/api/v1/runs/{run_id}").json()["phase"] == "terminal"
