"""Retained-history retention sweeper (ADR-0015).

The core keeps only the *minimum* run history the "What's Interesting" view needs
— commit provenance (#10) and flaky detection (#11) — so history cannot grow
forever across ~100 targets validated nightly and on every CI deploy. This sweeper
enforces a **single configurable rolling window** (``RETENTION_WINDOW_DAYS``,
default 90) over the atomic check-result record (:class:`CheckRun`), pruning rows
older than the window along with their child :class:`StageOutcome` rows.

The one load-bearing exception is the **preserved anchor**: the *most-recent green
(``PASS``) per ``(target, check)`` is never pruned, even when it predates the
window*. That single retained row is what keeps commit provenance honest — a check
that was green for months and then broke still diffs against the **right** last-good
commit, not the arbitrary edge of the window. (This is the opposite posture from the
audit log's unbounded retention, ADR-0014 — run/check volume is large, so it is
bounded; cost stays a non-goal, ADR-0007, so the window is one generous knob.)

The sweep runs on the existing background poller cadence, beside the lease sweep
(see :mod:`app.workers.poller`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.enums import RunStatus
from app.core.ids import utcnow
from app.models import CheckRun, StageOutcome

logger = logging.getLogger(__name__)

#: Bind IN-clause chunks under SQLite's default variable limit (999).
_DELETE_CHUNK = 500


def _settings():
    from app.core.config import settings

    return settings


def _preserved_anchor_ids(session: Session) -> set[str]:
    """Run ids of the most-recent ``PASS`` per ``(target_id, check_id)`` series.

    Computed over *all* green history (not just the old rows), so the anchor is
    the true latest green — a green inside the window naturally supersedes an
    older one, which then becomes prunable. ``target_id`` may be ``None`` (an
    ad-hoc, target-unbound run); it is its own series key. Ties on ``created_at``
    break deterministically on ``run_id`` so repeated sweeps agree.
    """
    anchors: dict[tuple[Optional[str], str], tuple[datetime, str]] = {}
    greens = session.exec(
        select(CheckRun.run_id, CheckRun.target_id, CheckRun.check_id, CheckRun.created_at)
        .where(CheckRun.verdict == RunStatus.PASS)
    ).all()
    for run_id, target_id, check_id, created_at in greens:
        key = (target_id, check_id)
        current = anchors.get(key)
        if current is None or (created_at, run_id) > current:
            anchors[key] = (created_at, run_id)
    return {run_id for _, run_id in anchors.values()}


def sweep(session: Session, *, window_days: Optional[int] = None,
          now: Optional[datetime] = None) -> int:
    """Prune check-result history older than the window; return rows pruned.

    Deletes every :class:`CheckRun` (and its :class:`StageOutcome` children)
    created before ``now - window_days``, **except** the most-recent ``PASS`` per
    ``(target, check)`` series, which is preserved as the provenance anchor
    regardless of age. ``window_days`` / ``now`` default to the configured
    ``RETENTION_WINDOW_DAYS`` and wall clock; both are overridable for the tests.
    """
    days = _settings().RETENTION_WINDOW_DAYS if window_days is None else window_days
    now = now or utcnow()
    cutoff = now - timedelta(days=days)

    old_ids = [
        run_id for run_id in session.exec(
            select(CheckRun.run_id).where(CheckRun.created_at < cutoff)
        ).all()
    ]
    if not old_ids:
        return 0

    preserved = _preserved_anchor_ids(session)
    to_delete = [run_id for run_id in old_ids if run_id not in preserved]
    if not to_delete:
        return 0

    # Children first (SQLite does not enforce the FK cascade), both chunked to
    # stay under the bound-parameter limit on a first sweep of a large backlog.
    # ``synchronize_session="fetch"`` expunges the deleted rows from the session's
    # identity map, so a caller sharing this session (the tests) reads a
    # consistent view rather than a stale, since-deleted object.
    opts = {"synchronize_session": "fetch"}
    for start in range(0, len(to_delete), _DELETE_CHUNK):
        chunk = to_delete[start:start + _DELETE_CHUNK]
        session.execute(delete(StageOutcome).where(StageOutcome.run_id.in_(chunk)), execution_options=opts)
        session.execute(delete(CheckRun).where(CheckRun.run_id.in_(chunk)), execution_options=opts)
    session.commit()

    logger.info("Pruned %d check-result row(s) older than %d day(s); "
                "%d green anchor(s) preserved.", len(to_delete), days, len(preserved))
    return len(to_delete)
