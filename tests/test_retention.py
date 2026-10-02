"""Service-layer seam for the retained-history retention sweeper (spec #32,
ADR-0015).

The sweeper's contract has one load-bearing edge case — **the most-recent green
per `(target, check)` is never pruned, even when it predates the window** — that
is tedious to stage end-to-end but trivial to assert directly over synthetic
CheckRun history. So the prune/preserve matrix is unit-tested here on the
service seam, and the history-completeness half (a run header carrying a
non-deploy event ref alongside the commit sha) is asserted through the run
service's submit → expand path.
"""
from __future__ import annotations

from datetime import timedelta

from sqlmodel import select

from app.core.enums import RequestSource, RunStatus
from app.core.ids import utcnow
from app.models import Check, CheckRun, StageOutcome
from app.schemas.schemas import RunSubmit
from app.services import retention, run_service


def _check_run(session, *, verdict, target_id, check_id="chk-1", age_days=0.0,
               with_stage=False) -> str:
    """Persist one CheckRun aged ``age_days`` into the past (0 = now); return its id.

    Returning the id (not the ORM object) keeps assertions robust against the
    sweep deleting the row out from under a held reference.
    """
    created = utcnow() - timedelta(days=age_days)
    run = CheckRun(check_id=check_id, target_id=target_id, verdict=verdict,
                   gating=True, created_at=created)
    session.add(run)
    session.commit()
    run_id = run.run_id
    if with_stage:
        session.add(StageOutcome(run_id=run_id, stage="OPERATE", sort_order=0))
        session.commit()
    return run_id


def _surviving_ids(session) -> set[str]:
    return {r.run_id for r in session.exec(select(CheckRun)).all()}


# --------------------------- prune the old, keep the recent ---------------------------
def test_sweep_prunes_history_older_than_the_window(session):
    # A non-green result well outside the 90-day window is pruned, and its child
    # stage outcomes go with it (no orphaned rows).
    old = _check_run(session, verdict=RunStatus.FAIL, target_id="t1", age_days=120,
                     with_stage=True)

    assert retention.sweep(session) == 1
    assert session.get(CheckRun, old) is None
    assert session.exec(
        select(StageOutcome).where(StageOutcome.run_id == old)
    ).all() == []


def test_sweep_keeps_rows_within_the_window(session):
    # A result inside the window is untouched, whatever its verdict.
    recent = _check_run(session, verdict=RunStatus.FAIL, target_id="t1", age_days=10)

    assert retention.sweep(session) == 0
    assert session.get(CheckRun, recent) is not None


# ------------------------- the preserved last-green anchor -------------------------
def test_latest_green_per_series_is_never_pruned_even_when_it_predates_the_window(session):
    # The one anchor: a check green for months then broken still diffs the *right*
    # commit, not the window edge. The lone green, though older than the window,
    # survives; the newer red that predates the window is pruned.
    green = _check_run(session, verdict=RunStatus.PASS, target_id="t1", age_days=200)
    red = _check_run(session, verdict=RunStatus.FAIL, target_id="t1", age_days=100)

    assert retention.sweep(session) == 1
    assert _surviving_ids(session) == {green}
    assert session.get(CheckRun, red) is None


def test_only_the_most_recent_green_in_a_series_is_anchored(session):
    # A superseded green (an older green with a newer green in the same series) is
    # not the anchor and IS pruned once outside the window.
    old_green = _check_run(session, verdict=RunStatus.PASS, target_id="t1", age_days=200)
    newer_green = _check_run(session, verdict=RunStatus.PASS, target_id="t1", age_days=120)

    assert retention.sweep(session) == 1
    assert _surviving_ids(session) == {newer_green}
    assert session.get(CheckRun, old_green) is None


def test_anchor_is_preserved_per_series(session):
    # Each (target, check) keeps its own latest green independently — one series'
    # anchor never covers another's.
    a = _check_run(session, verdict=RunStatus.PASS, target_id="t1", check_id="c1", age_days=200)
    b = _check_run(session, verdict=RunStatus.PASS, target_id="t2", check_id="c1", age_days=200)
    c = _check_run(session, verdict=RunStatus.PASS, target_id="t1", check_id="c2", age_days=200)

    assert retention.sweep(session) == 0
    assert _surviving_ids(session) == {a, b, c}


def test_a_series_with_no_green_has_no_anchor(session):
    # No green ever ⇒ nothing to preserve; every old row prunes.
    r1 = _check_run(session, verdict=RunStatus.FAIL, target_id="t1", age_days=200)
    r2 = _check_run(session, verdict=RunStatus.ERROR, target_id="t1", age_days=150)

    assert retention.sweep(session) == 2
    assert _surviving_ids(session) == set()
    assert session.get(CheckRun, r1) is None
    assert session.get(CheckRun, r2) is None


def test_an_in_window_green_supersedes_the_old_anchor(session):
    old_green = _check_run(session, verdict=RunStatus.PASS, target_id="t1", age_days=200)
    fresh_green = _check_run(session, verdict=RunStatus.PASS, target_id="t1", age_days=1)

    assert retention.sweep(session) == 1
    assert _surviving_ids(session) == {fresh_green}
    assert session.get(CheckRun, old_green) is None


def test_target_less_runs_are_their_own_series(session):
    # A target-unbound green is anchored on its own; a targeted green of the same
    # check neither covers it nor is covered by it.
    unbound = _check_run(session, verdict=RunStatus.PASS, target_id=None, age_days=200)
    bound_old = _check_run(session, verdict=RunStatus.PASS, target_id="t1", age_days=200)
    bound_new = _check_run(session, verdict=RunStatus.PASS, target_id="t1", age_days=150)

    assert retention.sweep(session) == 1
    assert _surviving_ids(session) == {unbound, bound_new}
    assert session.get(CheckRun, bound_old) is None


def test_anchor_ties_on_created_at_break_on_run_id(session):
    created = utcnow() - timedelta(days=200)
    for run_id in ("run-a", "run-b"):
        session.add(CheckRun(run_id=run_id, check_id="chk-1", target_id="t1",
                             verdict=RunStatus.PASS, created_at=created))
    session.commit()

    assert retention.sweep(session) == 1
    assert _surviving_ids(session) == {"run-b"}
    assert retention.sweep(session) == 0


def test_a_sweep_with_only_preserved_anchors_reads_one_row_and_writes_nothing(session):
    # Anchors are normal (a deregistered target, a long-red check). Once they are
    # all that is old, a sweep must not pull green history into memory again.
    from sqlalchemy import event

    from app.db.session import get_engine

    for n in range(20):
        _check_run(session, verdict=RunStatus.PASS, target_id=f"gone-{n}", age_days=200)
    for n in range(50):
        _check_run(session, verdict=RunStatus.PASS, target_id="t1", age_days=n / 10)

    seen: list[str] = []

    def _capture(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        seen.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        assert retention.sweep(session) == 0
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert len(seen) == 1 and "LIMIT" in seen[0].upper(), seen


# ------------------------------- configurable window -------------------------------
def test_window_is_configurable(session):
    # A tighter window prunes a row the default would keep — the single knob is
    # honored (default 90 days; here overridden to 30 at the seam).
    row = _check_run(session, verdict=RunStatus.FAIL, target_id="t1", age_days=45)

    assert retention.sweep(session) == 0  # within the 90-day default
    assert retention.sweep(session, window_days=30) == 1
    assert session.get(CheckRun, row) is None


# ----------------------- history completeness: the event ref -----------------------
def test_run_header_carries_a_non_deploy_event_ref_alongside_the_commit_sha(session):
    # A submitted run header records the non-deploy event ref in addition to the
    # commit sha it already carries, and that ref is denormalized onto each atomic
    # CheckRun so an Overnight finding has a provenance spine with no commit.
    chk = Check(display_name="probe",
                operation={"service": "jobs", "method": "list", "verb": "GET"})
    session.add(chk)
    session.commit()
    session.refresh(chk)

    payload = RunSubmit(check_ids=[chk.id], request_source=RequestSource.NIGHTLY,
                        event_ref="metastore grant change · 21:14")
    request = run_service.submit_request(session, payload)
    assert request.event_ref == "metastore grant change · 21:14"
    assert request.git_sha  # the commit sha it already carries is untouched

    runs = run_service.expand_request(session, request)
    assert runs and all(r.event_ref == "metastore grant change · 21:14" for r in runs)


def test_event_ref_defaults_to_none_for_an_ordinary_run(session):
    # An ordinary CI run carries no event ref — it diffs against its commit sha.
    payload = RunSubmit()
    request = run_service.submit_request(session, payload)
    assert request.event_ref is None
