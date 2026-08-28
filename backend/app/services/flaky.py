"""Flaky-check detection — advisory only (#34, #11, ADR-0008).

A check whose recent verdicts *alternate* between green and bad is **flaky**: its
signal is intermittent, not a clean regression. The "What's Interesting" view
flags this so a triager doesn't chase an alternating check as a hard regression —
but the flag is **advisory only**. A flaky advisory can never turn a finding into
a gating regression and can never block a pipeline (ADR-0008); it rides *beside*
the verdict, never changing whether it gates.

Two deliberate scoping choices, both from the spec:

* **Deterministic verdicts only.** Flaky is about the *deterministic* signal
  alternating. ``agentic`` results are already advisory (they never gate), so
  folding them in would conflate two different kinds of advisory noise — they are
  excluded from the scan.

* **Alternation, not a single regression.** A clean ``green → bad`` (one
  transition) is a regression, *not* flaky. Flakiness needs the verdict to swing
  back and forth — **at least two transitions** across the recent window — so
  ``green → bad → green`` (recovered-then-broke) or ``bad → green → bad`` counts,
  a stays-bad regression does not.

This is a strictly read-only computation over retained :class:`CheckRun` history.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.core.enums import Evaluator, RunStatus
from app.models import CheckRun

#: Verdicts that read as "bad" (a green is only ``PASS``). Non-verdict terminal
#: states (``SKIPPED`` / ``CANCELLED``) and non-terminal phases are not part of
#: the pass/bad alternation and are excluded from the scan entirely.
_BAD = frozenset({RunStatus.FAIL, RunStatus.ERROR, RunStatus.BLOCKED_BY_SAFETY})

#: Alternation needs the verdict to swing back — one transition is a regression.
_MIN_TRANSITIONS = 2


def count_transitions(greens: list[bool]) -> int:
    """Number of times an ordered green/bad sequence flips state.

    Order-independent for the flaky verdict (a sequence and its reverse flip the
    same number of times), so the caller may pass newest- or oldest-first.
    """
    return sum(1 for a, b in zip(greens, greens[1:]) if a != b)


def _series_verdicts(session: Session, *, check_id: str,
                     target_id: Optional[str], window: int) -> list[RunStatus]:
    """The last ``window`` DETERMINISTIC pass/bad verdicts, newest first.

    Only ``PASS`` and the ``_BAD`` verdicts participate in alternation; other
    terminal states and in-flight runs are filtered out before the window is
    taken, so a run of ``SKIPPED`` rows can't dilute the transition count.
    """
    stmt = select(CheckRun.verdict).where(
        CheckRun.check_id == check_id,
        CheckRun.evaluator == Evaluator.DETERMINISTIC,
    )
    if target_id is not None:
        stmt = stmt.where(CheckRun.target_id == target_id)
    stmt = stmt.order_by(CheckRun.created_at.desc())  # type: ignore[attr-defined]

    verdicts: list[RunStatus] = []
    for verdict in session.exec(stmt).all():
        if verdict == RunStatus.PASS or verdict in _BAD:
            verdicts.append(verdict)
        if len(verdicts) >= window:
            break
    return verdicts


def flaky_report(session: Session, *, check_id: str, target_id: Optional[str] = None,
                 window: Optional[int] = None) -> dict:
    """Advisory flaky assessment for a ``(target, check)`` series — read-only.

    ``window`` bounds how many recent deterministic verdicts are scanned; it
    defaults to the configured ``FLAKY_WINDOW_RUNS``. The result is advisory
    signal only — it never gates (``advisory`` is always ``True``).
    """
    from app.core.config import get_settings

    window = window if window is not None else getattr(
        get_settings(), "FLAKY_WINDOW_RUNS", 10)
    verdicts = _series_verdicts(session, check_id=check_id, target_id=target_id,
                                window=window)
    transitions = count_transitions([v == RunStatus.PASS for v in verdicts])
    return {
        "flaky": transitions >= _MIN_TRANSITIONS,
        "transitions": transitions,
        "considered": len(verdicts),
        "advisory": True,
    }


def is_flaky(session: Session, *, check_id: str, target_id: Optional[str] = None,
             window: Optional[int] = None) -> bool:
    """Whether a series' recent deterministic verdicts alternate (advisory)."""
    return flaky_report(session, check_id=check_id, target_id=target_id,
                        window=window)["flaky"]
