"""Service-layer seam for the per-target run lock (spec #21, ADR-0011).

The lease/heartbeat/queue/max-duration matrix is timing-sensitive and awkward to
drive end-to-end, so it is unit-tested directly here (spec Testing Decisions).
Assertions are on the observable lock behaviour — who holds it, whether a second
holder is refused (→ the run queues), and whether a lapsed / wedged lease frees.
"""
from __future__ import annotations

from datetime import timedelta

from app.core.enums import SafetyClass
from app.core.ids import utcnow
from app.models import TargetLease
from app.services import locking


# --------------------------- safety-class gating ---------------------------
def test_read_only_targets_never_lock():
    # A read-only target has no mutation to interfere, so it never locks —
    # preserving the fully-concurrent ~100-target read-only CI path (ADR-0011 §1).
    assert locking.needs_lock(SafetyClass.READ_ONLY) is False
    assert locking.needs_lock(SafetyClass.SANDBOX) is True
    assert locking.needs_lock(SafetyClass.PROD_FOR_US) is True


def test_read_only_run_creates_no_lease(session):
    # needs_lock is False, so the engine never even attempts to lock — assert the
    # bypass leaves the lease table empty for a read-only target.
    assert locking.needs_lock(SafetyClass.READ_ONLY) is False
    from sqlmodel import select
    assert session.exec(select(TargetLease)).all() == []


# ------------------------------ serialization ------------------------------
def test_second_holder_is_refused_so_the_run_queues(session):
    assert locking.acquire(session, "tgt-1", holder="req-a") is True
    # A different run selecting the same locked target is refused acquisition —
    # the engine turns that into a `queued` phase, never a refusal (ADR-0011 §2).
    assert locking.acquire(session, "tgt-1", holder="req-b") is False
    # The first holder still owns it.
    lease = session.get(TargetLease, "tgt-1")
    assert lease is not None and lease.holder == "req-a"


def test_acquire_is_reentrant_for_the_same_holder(session):
    assert locking.acquire(session, "tgt-1", holder="req-a") is True
    # Re-acquiring as the same holder is a no-op success (and refreshes heartbeat),
    # so a retried dispatch of the same run never deadlocks itself.
    before = session.get(TargetLease, "tgt-1").heartbeat_at
    assert locking.acquire(session, "tgt-1", holder="req-a") is True
    assert session.get(TargetLease, "tgt-1").heartbeat_at >= before


def test_release_frees_the_target_for_the_next_run(session):
    locking.acquire(session, "tgt-1", holder="req-a")
    assert locking.release(session, "tgt-1", holder="req-a") is True
    # Freed: the next queued run acquires cleanly.
    assert locking.acquire(session, "tgt-1", holder="req-b") is True


def test_release_by_a_non_holder_is_a_no_op(session):
    locking.acquire(session, "tgt-1", holder="req-a")
    assert locking.release(session, "tgt-1", holder="req-b") is False
    assert session.get(TargetLease, "tgt-1").holder == "req-a"


# ------------------------------- heartbeat ---------------------------------
def test_heartbeat_extends_the_lease(session):
    locking.acquire(session, "tgt-1", holder="req-a", ttl_seconds=90)
    lease = session.get(TargetLease, "tgt-1")
    # Simulate an aged lease, then heartbeat and confirm the expiry moved forward.
    old_expiry = utcnow() - timedelta(seconds=10)
    lease.expires_at = old_expiry
    session.add(lease)
    session.commit()

    assert locking.heartbeat(session, "tgt-1", holder="req-a", ttl_seconds=90) is True
    assert session.get(TargetLease, "tgt-1").expires_at > old_expiry


def test_heartbeat_by_a_non_holder_is_refused(session):
    locking.acquire(session, "tgt-1", holder="req-a")
    assert locking.heartbeat(session, "tgt-1", holder="req-b") is False


# ------------------- lapsed TTL / max-duration reclaim ----------------------
def test_lapsed_lease_is_reclaimable_by_the_next_run(session):
    # A crashed holder stops heartbeating; once the TTL lapses the lease is
    # reclaimable so the target can never deadlock forever (ADR-0011 §5).
    locking.acquire(session, "tgt-1", holder="dead", ttl_seconds=0)
    # ttl=0 → already lapsed; a fresh acquire reclaims it.
    assert locking.acquire(session, "tgt-1", holder="req-b") is True
    assert session.get(TargetLease, "tgt-1").holder == "req-b"


def test_sweep_reclaims_lapsed_and_wedged_leases(session):
    # Lapsed heartbeat (crash) and exceeded max-run-duration (wedge) both free.
    locking.acquire(session, "crashed", holder="req-a", ttl_seconds=0)
    locking.acquire(session, "wedged", holder="req-b", max_duration_seconds=0)
    locking.acquire(session, "healthy", holder="req-c", ttl_seconds=90,
                    max_duration_seconds=1800)

    reclaimed = locking.sweep(session)
    assert set(reclaimed) == {"crashed", "wedged"}
    # The healthy, heartbeating, within-deadline lease is untouched.
    assert session.get(TargetLease, "healthy") is not None
    assert session.get(TargetLease, "crashed") is None
    assert session.get(TargetLease, "wedged") is None


def test_max_run_duration_frees_even_a_heartbeating_lease(session):
    # The hard deadline reclaims a run that keeps heartbeating but overruns the
    # server-side max duration — distinct from the caller-owned timeout.
    locking.acquire(session, "tgt-1", holder="req-a", ttl_seconds=90,
                    max_duration_seconds=0)
    # Heartbeat keeps the TTL fresh, but the deadline has already passed.
    locking.heartbeat(session, "tgt-1", holder="req-a", ttl_seconds=90)
    assert locking.sweep(session) == ["tgt-1"]
