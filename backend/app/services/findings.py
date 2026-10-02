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

Each finding states its failure as ``bucket @ stage`` (ADR-0001) and carries its
**troubleshooting spine** (#34): the commit-provenance thread (last-green →
first-bad, keyed to a commit sha or a non-deploy event ref, and honest even when
the last green predates the retention window — the sweeper's preserved anchor)
and an **advisory** ``flaky`` flag for a check whose recent deterministic
verdicts alternate. Flaky is advisory only — it never turns a finding into a
gating regression (ADR-0008). This is a strictly read-only computation over
history — it never mutates a run.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.enums import RunStatus, RequestSource, TERMINAL_STATUSES
from app.core.ids import utcnow
from app.core.taxonomy import bucket_at_stage, get_bucket
from app.models import Check, CheckRun, Suite, SuiteMember, Test, TestCheck
from app.services import flaky as flaky_service
from app.services.assistant_tools import commit_provenance_thread, commit_provenance_threads
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


#: The CheckRun columns a series head is classified and rendered from — never the
#: evidence blob, which the feed doesn't return.
_HEAD_COLUMNS = (
    CheckRun.run_id, CheckRun.check_id, CheckRun.target_id, CheckRun.test_id,
    CheckRun.request_id, CheckRun.workspace_name, CheckRun.verdict,
    CheckRun.failure_bucket, CheckRun.failure_stage, CheckRun.gating,
    CheckRun.evaluator, CheckRun.request_source, CheckRun.git_sha,
    CheckRun.message, CheckRun.finished_at, CheckRun.created_at,
)


def _latest_terminal_per_series(session: Session) -> list:
    """The newest terminal CheckRun for each ``(target_id, check_id)`` series.

    A series' *current* state is its most recent terminal run; in-flight runs
    (PENDING/QUEUED/RUNNING) don't change what a triager currently faces, so they
    are ignored. Rows carry :data:`_HEAD_COLUMNS` as attributes.
    """
    newest_first = func.row_number().over(
        partition_by=(CheckRun.target_id, CheckRun.check_id),
        order_by=(CheckRun.created_at.desc(), CheckRun.run_id.desc()),  # type: ignore[union-attr]
    ).label("series_rank")
    ranked = (
        select(*_HEAD_COLUMNS, newest_first)
        .where(CheckRun.verdict.in_(sorted(TERMINAL_STATUSES)))  # type: ignore[attr-defined]
        .subquery()
    )
    return list(session.exec(
        select(*(ranked.c[c.key] for c in _HEAD_COLUMNS)).where(ranked.c.series_rank == 1)
    ).all())


def _spines(session: Session, runs: list) -> tuple[dict, dict]:
    """Provenance threads + flaky flags for the given series heads, batched.

    A target-less series reads "any target" in the single-series tools, so it
    keeps that path; every targeted series is answered in a fixed number of
    statements regardless of how many findings or how much history there is.
    """
    targeted = [(r.target_id, r.check_id) for r in runs if r.target_id]
    provenance = commit_provenance_threads(session, targeted)
    flaky = flaky_service.flaky_flags(session, targeted)
    for r in runs:
        if not r.target_id:
            key = (r.target_id, r.check_id)
            provenance[key] = commit_provenance_thread(session, check_id=r.check_id,
                                                       target_id=r.target_id)
            flaky[key] = flaky_service.is_flaky(session, check_id=r.check_id,
                                                target_id=r.target_id)
    return provenance, flaky


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


def _catalog_index(session: Session) -> dict:
    """Tests/Suites keyed for finding placement — one pass, then O(1) lookups.

    A finding is a Check result; the triager needs the Test it belongs to (to
    edit) and the Suites that contain that Test (Suites have no page of their
    own — they are a filter on Tests). When a Check is shared, the CheckRun's
    ``test_id`` wins; otherwise every Test that lists the Check.
    """
    tests = {t.id: t for t in session.exec(select(Test)).all()}
    suites = {s.id: s for s in session.exec(select(Suite)).all()}
    check_to_tests: dict[str, list[str]] = {}
    for tc in session.exec(select(TestCheck)).all():
        check_to_tests.setdefault(tc.check_id, []).append(tc.test_id)
    test_to_suites: dict[str, list[str]] = {}
    for m in session.exec(select(SuiteMember)).all():
        test_to_suites.setdefault(m.test_id, []).append(m.suite_id)
    return {
        "tests": tests,
        "suites": suites,
        "check_to_tests": check_to_tests,
        "test_to_suites": test_to_suites,
    }


def _placement(run: CheckRun, catalog: dict) -> dict:
    tests: dict = catalog["tests"]
    if run.test_id and run.test_id in tests:
        members = [tests[run.test_id]]
    else:
        members = [tests[tid] for tid in catalog["check_to_tests"].get(run.check_id, [])
                   if tid in tests]
    suite_ids: list[str] = []
    seen: set[str] = set()
    for t in members:
        for sid in catalog["test_to_suites"].get(t.id, []):
            if sid not in seen and sid in catalog["suites"]:
                seen.add(sid)
                suite_ids.append(sid)
    return {
        "tests": [{"id": t.id, "name": t.name} for t in members],
        "suites": [{"id": sid, "name": catalog["suites"][sid].name} for sid in suite_ids],
        "request_id": run.request_id,
    }


def _failure_label(bucket_id: Optional[str]) -> Optional[str]:
    bucket = get_bucket(bucket_id or "")
    return bucket.label if bucket else None


def _finding(run: CheckRun, check_name: str, lane: str, catalog: dict,
             provenance: dict, flaky: bool) -> dict:
    """Render one finding — the current failing state of a series.

    Each finding carries its **troubleshooting spine**: the commit-provenance
    thread (last-green → first-bad, keyed to a commit sha or event ref, #34) and
    an **advisory** ``flaky`` flag. Flaky rides *beside* ``gating`` and never
    changes it — an alternating check is signal, never a hard/gating regression
    (ADR-0008).
    """
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
        "failure_label": _failure_label(run.failure_bucket),
        "lane": lane,
        # Gating is decided by the check alone; flaky never flips it.
        "gating": run.gating,
        "evaluator": run.evaluator.value,
        "request_source": run.request_source.value,
        "git_sha": run.git_sha,
        "message": run.message,
        # The last-green → first-bad troubleshooting spine (#34).
        "provenance": provenance,
        # Advisory only — never turns the finding into a gating regression.
        "flaky": flaky,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        **_placement(run, catalog),
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
    catalog = _catalog_index(session)
    lanes: dict[str, dict] = {name: {"findings": [], "everything_else": 0}
                              for name in _LANE_ORDER}

    heads = _latest_terminal_per_series(session)
    provenance, flaky = _spines(session, [r for r in heads if r.verdict in _ACTIONABLE])
    for run in heads:
        lane = classify_lane(run, now=now, window=window)
        if run.verdict in _ACTIONABLE:
            name = checks[run.check_id].display_name if run.check_id in checks else run.check_id
            key = (run.target_id, run.check_id)
            lanes[lane]["findings"].append(
                _finding(run, name, lane, catalog, provenance[key], flaky[key]))
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
