"""Suite composition safety guards (issue #63, spec #56)."""
from __future__ import annotations

from sqlmodel import Session, select

from app.core.enums import RequestSource
from app.models import RunRequest


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
