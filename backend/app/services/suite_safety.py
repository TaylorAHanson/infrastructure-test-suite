"""Suite composition safety guards (issue #63, spec #56)."""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.core.enums import Evaluator, RequestSource
from app.models import Check, RunRequest, Suite, TestCheck


def agentic_checks_in_tests(session: Session, test_ids: list[str]) -> list[str]:
    """Return the subset of test_ids that contain at least one agentic Check."""
    culprits: list[str] = []
    for tid in test_ids:
        members = session.exec(
            select(TestCheck).where(TestCheck.test_id == tid)  # type: ignore[arg-type]
        ).all()
        for m in members:
            check = session.get(Check, m.check_id)
            if check and check.evaluator == Evaluator.AGENTIC:
                culprits.append(tid)
                break
    return culprits


def check_deterministic_violation(
    session: Session,
    suite: Suite,
    proposed_test_ids: list[str],
    proposed_deterministic_only: Optional[bool] = None,
) -> Optional[str]:
    """Return refusal reason if the proposal would break deterministic_only, else None.

    Invariant: a deterministic_only Suite may only contain Tests whose Checks are
    ALL deterministic. If any proposed Test has an agentic Check and the Suite is
    (or would be) deterministic_only, the change is refused.
    """
    will_be_det_only = (
        proposed_deterministic_only
        if proposed_deterministic_only is not None
        else suite.deterministic_only
    )
    if not will_be_det_only:
        return None
    culprits = agentic_checks_in_tests(session, proposed_test_ids)
    if not culprits:
        return None
    return (
        f"Test(s) {culprits} contain agentic Checks. Adding them to a "
        "deterministic_only Suite would silently make its fail-closed verdict "
        "non-deterministic. Remove the agentic Checks or unset deterministic_only first."
    )


def is_suite_referenced_by_nightly_job(session: Session, suite_id: str) -> bool:
    """True if this suite was included in any past NIGHTLY run request.

    A NIGHTLY RunRequest with empty suite_ids means "all suites", so this suite
    is always implicitly included. A NIGHTLY RunRequest with explicit suite_ids
    includes this suite only when suite_id appears in the list.
    """
    nightly = session.exec(
        select(RunRequest).where(
            RunRequest.request_source == RequestSource.NIGHTLY  # type: ignore[arg-type]
        )
    ).all()
    for req in nightly:
        if not req.suite_ids or suite_id in req.suite_ids:
            return True
    return False
