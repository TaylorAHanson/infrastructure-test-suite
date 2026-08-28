"""Service seam: flaky-check detection (#34/#11, ADR-0008).

Flaky is **advisory only** — an alternating deterministic verdict is signal, not
a hard/gating regression. These cases pin the alternation contract directly over
synthetic CheckRun history: a clean regression is not flaky, an alternating one
is, agentic verdicts are excluded, and the flag never gates.
"""
from __future__ import annotations

from datetime import timedelta

from app.core.enums import Evaluator, RunStatus
from app.core.ids import utcnow
from app.models import Check, CheckRun, Target
from app.services import flaky


def _series(session, verdicts: list, *, evaluator=Evaluator.DETERMINISTIC):
    """Seed a (check, target) with one run per verdict, oldest first."""
    check = Check(display_name="probe", assertion="the operation succeeds")
    target = Target(name="t1", workspace_name="ws1")
    session.add(check)
    session.add(target)
    session.commit()
    session.refresh(check)
    session.refresh(target)
    n = len(verdicts)
    for i, verdict in enumerate(verdicts):
        ts = utcnow() - timedelta(minutes=(n - i) * 10)
        session.add(CheckRun(check_id=check.id, target_id=target.id, verdict=verdict,
                             evaluator=evaluator, created_at=ts))
    session.commit()
    return check, target


_G = RunStatus.PASS
_B = RunStatus.FAIL


# --------------------------- the pure alternation rule ---------------------------
def test_count_transitions_counts_state_flips():
    assert flaky.count_transitions([]) == 0
    assert flaky.count_transitions([True]) == 0
    assert flaky.count_transitions([True, False]) == 1            # a regression
    assert flaky.count_transitions([True, False, True]) == 2      # alternation
    assert flaky.count_transitions([True, False, True, False]) == 3


def test_count_transitions_is_order_independent():
    seq = [True, False, True, False]
    assert flaky.count_transitions(seq) == flaky.count_transitions(list(reversed(seq)))


# --------------------------- regression vs. flaky ---------------------------
def test_clean_regression_is_not_flaky(session):
    # green then bad-and-stays-bad → one transition → a regression, not flaky.
    check, target = _series(session, [_G, _G, _B, _B])
    assert flaky.is_flaky(session, check_id=check.id, target_id=target.id) is False


def test_alternating_verdicts_are_flaky(session):
    check, target = _series(session, [_G, _B, _G, _B])
    report = flaky.flaky_report(session, check_id=check.id, target_id=target.id)
    assert report["flaky"] is True
    assert report["transitions"] >= 2
    assert report["advisory"] is True


def test_recovered_then_broke_is_flaky(session):
    # green → bad → green → bad reads as intermittent, not a single regression.
    check, target = _series(session, [_G, _B, _G, _B, _G])
    assert flaky.is_flaky(session, check_id=check.id, target_id=target.id) is True


# --------------------------- scope: deterministic only, windowed ---------------------------
def test_agentic_verdicts_excluded_from_flaky(session):
    # The same alternating sequence, but recorded by the AGENTIC evaluator, is not
    # counted — flaky is about the DETERMINISTIC signal alternating (ADR-0008).
    check, target = _series(session, [_G, _B, _G, _B], evaluator=Evaluator.AGENTIC)
    report = flaky.flaky_report(session, check_id=check.id, target_id=target.id)
    assert report["flaky"] is False
    assert report["considered"] == 0


def test_non_verdict_terminals_do_not_dilute_alternation(session):
    # SKIPPED/CANCELLED are not pass/bad and must not count as transitions:
    # green, skipped, bad → still one green→bad transition, not flaky.
    check, target = _series(session, [_G, RunStatus.SKIPPED, _B])
    assert flaky.is_flaky(session, check_id=check.id, target_id=target.id) is False


def test_window_bounds_how_much_history_is_scanned(session):
    # Old alternation falls out of a small window; the recent tail is stable.
    check, target = _series(session, [_G, _B, _G, _B, _G, _G, _G])
    assert flaky.is_flaky(session, check_id=check.id, target_id=target.id, window=3) is False
    assert flaky.is_flaky(session, check_id=check.id, target_id=target.id, window=10) is True
