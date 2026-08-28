"""The findings feed — retained run history → a ranked triage feed (#33, ADR-0009).

The "What's Interesting" view shows **findings, not raw runs**: the reader wants
*"what regressed, and why"*, not 100 green rows. This service turns the retained
CheckRun history into that feed.

* **Grain — one finding per ``(target, check)`` series.** A series is the run
  history of one Check against one Target; its *current* state is the latest
  **terminal** run (:data:`TERMINAL_STATUSES`). A series is surfaced **once**, as
  its current state — a check failing five runs running is one finding, not five.

* **Actionable vs. not.** A series whose latest terminal run is a failure
  (``FAIL`` / ``ERROR`` / ``BLOCKED_BY_SAFETY``) is a **finding**. Everything else
  — nominal (still green) or recovered (green→bad→green) — is **not** a finding;
  it collapses into a single **"Everything else"** count per lane (ADR-0009 §6:
  no non-actionable numbers presented as findings).

* **Two lanes, one screen (ADR-0009 §2/§3).** A series lands in
  **Recent-deployments** only when its current state is **deploy-attributable** —
  CI-triggered, commit-scoped, and within the rolling window. Absent that, it
  defaults to **Overnight**. Lane placement here is the deterministic floor; the
  Assistant's RCA (ADR-0012) may *suggest* a richer attribution on top, but never
  moves a finding off the Overnight default without a commit-backed reason.

Each finding states its failure as ``bucket @ stage`` (ADR-0001). This is a
strictly read-only computation over history — it never mutates a run.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.enums import RunStatus, RequestSource, TERMINAL_STATUSES
from app.core.ids import utcnow
from app.core.taxonomy import bucket_at_stage
from app.models import Check, CheckRun
from app.services.rca import LANE_OVERNIGHT, LANE_RECENT

#: A failing current state that a triager should act on. The other terminal
#: verdicts (PASS / SKIPPED / CANCELLED) are non-actionable → "Everything else".
#: This is *the* definition of a finding's actionability — the run-completion
#: webhook (ADR-0016) reuses it verbatim so "≥ 1 finding" means exactly what the
#: "What's Interesting" view surfaces, inventing no new regression logic.
ACTIONABLE_VERDICTS = frozenset({RunStatus.FAIL, RunStatus.ERROR, RunStatus.BLOCKED_BY_SAFETY})
_ACTIONABLE = ACTIONABLE_VERDICTS

#: Lane order for the feed — Recent-deployments first, Overnight second
#: (the two-lanes-on-one-screen reading order, ADR-0009 §2).
_LANE_ORDER = (LANE_RECENT, LANE_OVERNIGHT)


def _recent_window_hours() -> int:
    # Read live settings each call (tests repoint config between cases).
    return getattr(get_settings(), "FINDINGS_RECENT_WINDOW_HOURS", 24)


def _latest_terminal_per_series(session: Session) -> list[CheckRun]:
    """The newest terminal CheckRun for each ``(target_id, check_id)`` series.

    A series' *current* state is its most recent terminal run; in-flight runs
    (PENDING/QUEUED/RUNNING) don't change what a triager currently faces, so they
    are ignored. Newest-first order lets the first row per key win.
    """
    rows = session.exec(
        select(CheckRun)
        .where(CheckRun.verdict.in_(list(TERMINAL_STATUSES)))  # type: ignore[attr-defined]
        .order_by(CheckRun.created_at.desc())  # type: ignore[union-attr]
    ).all()
    latest: dict[tuple[Optional[str], str], CheckRun] = {}
    for run in rows:
        latest.setdefault((run.target_id, run.check_id), run)
    return list(latest.values())


def classify_lane(run: CheckRun, *, now: datetime, window: timedelta) -> str:
    """Which lane a series' current state belongs to (ADR-0009 §3).

    **Recent-deployments** only when the result is attributable to a deploy:
    CI-triggered, carrying a real commit sha, and inside the rolling window.
    Anything else — a nightly/schedule/UI run, a missing sha, or a stale run —
    **defaults to Overnight**, so unattributed drift is never miscredited to a
    deploy.
    """
    deploy_attributed = (
        run.request_source == RequestSource.CI
        and bool(run.git_sha) and run.git_sha != "unknown"
        and run.created_at is not None
        and (now - run.created_at) <= window
    )
    return LANE_RECENT if deploy_attributed else LANE_OVERNIGHT


def _finding(run: CheckRun, check_name: str, lane: str) -> dict:
    """Render one finding — the current failing state of a series."""
    return {
        "check_id": run.check_id,
        "check_name": check_name,
        "target_id": run.target_id,
        "workspace_name": run.workspace_name,
        "run_id": run.run_id,
        "verdict": run.verdict.value,
        "failure_bucket": run.failure_bucket,
        "failure_stage": run.failure_stage,
        # Every finding states its failure as `bucket @ stage` (ADR-0001).
        "bucket_at_stage": bucket_at_stage(run.failure_bucket, run.failure_stage),
        "lane": lane,
        "gating": run.gating,
        "evaluator": run.evaluator.value,
        "request_source": run.request_source.value,
        "git_sha": run.git_sha,
        "message": run.message,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
    }


def build_feed(session: Session, *, window_hours: Optional[int] = None,
               limit: int = 200) -> dict:
    """Compute the two-lane findings feed over retained history.

    Each ``(target, check)`` series contributes exactly once: an actionable
    current state becomes a **finding** in its lane; a non-actionable one
    (nominal/recovered) increments that lane's **"Everything else"** count.
    Findings are ranked newest-first within each lane and capped at ``limit``.
    """
    hours = window_hours if window_hours is not None else _recent_window_hours()
    now = utcnow()
    window = timedelta(hours=hours)

    checks = {c.id: c for c in session.exec(select(Check)).all()}
    lanes: dict[str, dict] = {name: {"findings": [], "everything_else": 0}
                              for name in _LANE_ORDER}

    for run in _latest_terminal_per_series(session):
        lane = classify_lane(run, now=now, window=window)
        if run.verdict in _ACTIONABLE:
            name = checks[run.check_id].display_name if run.check_id in checks else run.check_id
            lanes[lane]["findings"].append(_finding(run, name, lane))
        else:
            lanes[lane]["everything_else"] += 1

    for bucket in lanes.values():
        bucket["findings"].sort(key=lambda f: f["created_at"], reverse=True)
        del bucket["findings"][limit:]

    return {
        "generated_at": now,
        "window_hours": hours,
        "lanes": [{"lane": name, **lanes[name]} for name in _LANE_ORDER],
    }
