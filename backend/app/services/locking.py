"""Per-target run locking — the target-level serialization lease (ADR-0011).

The run engine uses this to keep two *different* runs from trampling each
other's state on the same mutating target and producing untrustworthy verdicts
— the exact failure the platform exists to prevent. The design (ADR-0011):

* **Gated by safety class.** A ``read-only`` target has no mutation to interfere,
  so it **never locks** (:func:`needs_lock` is ``False``) — the fast, fully
  concurrent ~100-target read-only CI sweep is preserved. ``sandbox`` and
  ``prod-for-us`` targets **serialize**: at most one active Run per target.
* **Collision → the caller queues, never a refusal.** :func:`acquire` simply
  returns ``False`` when another run holds the target; the engine turns that into
  a non-terminal ``queued`` phase and retries later. The lock never surfaces as a
  Verdict, so the CI fail-closed mapping (ADR-0005) is untouched.
* **Crash / wedge release.** The holder renews its lease by :func:`heartbeat`
  while alive; :func:`sweep` reclaims a lease whose heartbeat has lapsed past the
  TTL (crash) **or** that has exceeded the server-side max run duration (wedge),
  so a dead or wedged holder can never deadlock a target forever.

The lease lives in the authoritative store (ADR-0001) as one :class:`TargetLease`
row per held target — **no external lock service** (Redis/ZooKeeper), keeping the
OSS story clean and correct across replicas.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.enums import SafetyClass
from app.core.ids import utcnow
from app.models import TargetLease

logger = logging.getLogger(__name__)


def _settings():
    from app.core.config import settings

    return settings


def needs_lock(safety_class: Optional[SafetyClass]) -> bool:
    """Whether a target of this class serializes runs (ADR-0011 §1).

    ``read-only`` (max R0) has no mutation to interfere and never locks; the
    mutating classes (``sandbox`` / ``prod-for-us``) do. An unregistered target
    (``None``) never reaches here as runnable, but is treated as non-locking.
    """
    return bool(safety_class is not None and safety_class.is_mutating)


def _is_expired(lease: TargetLease, now: datetime) -> bool:
    """A lease is reclaimable once its heartbeat lapses OR its deadline passes."""
    return now >= lease.expires_at or now >= lease.deadline_at


def acquire(
    session: Session,
    target_id: str,
    holder: str,
    *,
    ttl_seconds: Optional[int] = None,
    max_duration_seconds: Optional[int] = None,
) -> bool:
    """Try to acquire ``target_id``'s lease for ``holder`` (a RunRequest id).

    Returns ``True`` if the lease is now held by ``holder`` — either freshly
    acquired, reclaimed from a lapsed/wedged holder, or re-acquired re-entrantly
    by the same holder (which also refreshes its heartbeat). Returns ``False``
    when another live run holds it; the caller then **queues** (never refuses).
    """
    settings = _settings()
    ttl = settings.LEASE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    max_dur = (settings.MAX_RUN_DURATION_SECONDS
               if max_duration_seconds is None else max_duration_seconds)
    now = utcnow()

    lease = session.get(TargetLease, target_id)
    if lease is not None:
        if lease.holder == holder:
            # Re-entrant: refresh the heartbeat and keep the deadline.
            lease.heartbeat_at = now
            lease.expires_at = now + timedelta(seconds=ttl)
            session.add(lease)
            session.commit()
            return True
        if _is_expired(lease, now):
            # Reclaim a crashed/wedged holder's lease.
            session.delete(lease)
            session.commit()
        else:
            return False  # held by a live run → the caller queues

    lease = TargetLease(
        target_id=target_id, holder=holder, acquired_at=now, heartbeat_at=now,
        expires_at=now + timedelta(seconds=ttl),
        deadline_at=now + timedelta(seconds=max_dur),
    )
    session.add(lease)
    try:
        session.commit()
    except IntegrityError:
        # A concurrent worker inserted the row first — treat as "held elsewhere".
        session.rollback()
        return False
    return True


def heartbeat(
    session: Session, target_id: str, holder: str, *, ttl_seconds: Optional[int] = None
) -> bool:
    """Renew ``holder``'s lease TTL while the run is alive. No-op for a non-holder.

    The deadline (max run duration) is deliberately **not** extended — a
    heartbeating-but-wedged run still gets reclaimed by :func:`sweep`.
    """
    ttl = _settings().LEASE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    lease = session.get(TargetLease, target_id)
    if lease is None or lease.holder != holder:
        return False
    now = utcnow()
    lease.heartbeat_at = now
    lease.expires_at = now + timedelta(seconds=ttl)
    session.add(lease)
    session.commit()
    return True


def release(session: Session, target_id: str, holder: str) -> bool:
    """Release ``holder``'s lease so the next queued run can acquire it."""
    lease = session.get(TargetLease, target_id)
    if lease is None or lease.holder != holder:
        return False
    session.delete(lease)
    session.commit()
    return True


def sweep(session: Session, now: Optional[datetime] = None) -> list[str]:
    """Reclaim every lapsed/wedged lease. Returns the freed ``target_id``s.

    Called periodically by the poller (ADR-0011 §5) so a crashed holder (lapsed
    heartbeat) or a wedged run (past the max-run-duration deadline) frees its
    target for the queued runs behind it.
    """
    now = now or utcnow()
    reclaimed: list[str] = []
    for lease in session.exec(select(TargetLease)).all():
        if _is_expired(lease, now):
            reclaimed.append(lease.target_id)
            session.delete(lease)
    if reclaimed:
        session.commit()
        logger.info("Reclaimed %d lapsed/wedged target lease(s): %s",
                    len(reclaimed), reclaimed)
    return reclaimed
