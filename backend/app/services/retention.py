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

from sqlalchemy import and_, delete, exists, not_, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from app.core.enums import RunStatus
from app.core.ids import utcnow
from app.models import CheckRun, StageOutcome

logger = logging.getLogger(__name__)


def _settings():
    from app.core.config import settings

    return settings


def _is_preserved_anchor():
    """SQL predicate on ``CheckRun``: the row is the most-recent ``PASS`` of its series.

    True when no later green exists in the same ``(target_id, check_id)`` — so a
    green inside the window naturally supersedes an older one, which becomes
    prunable. ``target_id`` may be ``None`` (an ad-hoc, target-unbound run); it is
    its own series key. Ties on ``created_at`` break on ``run_id`` so repeated
    sweeps agree. Correlated per old green, it is answered from the
    ``(target_id, check_id, created_at)`` index rather than the series' history.
    """
    newer = aliased(CheckRun)
    same_series = and_(
        newer.check_id == CheckRun.check_id,
        or_(newer.target_id == CheckRun.target_id,
            and_(newer.target_id.is_(None), CheckRun.target_id.is_(None))),  # type: ignore[union-attr]
    )
    later = or_(newer.created_at > CheckRun.created_at,
                and_(newer.created_at == CheckRun.created_at, newer.run_id > CheckRun.run_id))
    return and_(
        CheckRun.verdict == RunStatus.PASS,
        not_(exists().where(same_series, later, newer.verdict == RunStatus.PASS)),
    )


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

    prunable = and_(CheckRun.created_at < cutoff, not_(_is_preserved_anchor()))
    if session.exec(select(CheckRun.run_id).where(prunable).limit(1)).first() is None:
        return 0

    # Children first (SQLite does not enforce the FK cascade).
    # ``synchronize_session="fetch"`` expunges the deleted rows from the session's
    # identity map, so a caller sharing this session (the tests) reads a
    # consistent view rather than a stale, since-deleted object.
    opts = {"synchronize_session": "fetch"}
    session.execute(
        delete(StageOutcome).where(StageOutcome.run_id.in_(select(CheckRun.run_id).where(prunable))),
        execution_options=opts)
    pruned = session.execute(delete(CheckRun).where(prunable), execution_options=opts).rowcount
    session.commit()

    logger.info("Pruned %d check-result row(s) older than %d day(s).", pruned, days)
    return pruned
