"""Validator-level tests for Suite composition safety (issue #63).

Mirrors the pattern in test_safety_gate.py: tests the validator directly at the
service layer, not only through the HTTP seam.
"""
from __future__ import annotations

from app.core.enums import RequestSource
from app.models import RunRequest, Suite
from app.services import suite_safety


# ---------------------------------------------------------------------------
# Nightly job detection
# ---------------------------------------------------------------------------
def test_nightly_detection_explicit_suite_id(session):
    suite = Suite(name="nightly-suite", description="")
    session.add(suite)
    session.commit()
    session.refresh(suite)
    req = RunRequest(suite_ids=[suite.id], request_source=RequestSource.NIGHTLY,
                     requested_by="nightly-dab")
    session.add(req)
    session.commit()
    assert suite_safety.is_suite_referenced_by_nightly_job(session, suite.id) is True


def test_nightly_detection_empty_suite_ids_means_all(session):
    suite = Suite(name="implicit-nightly", description="")
    session.add(suite)
    session.commit()
    session.refresh(suite)
    req = RunRequest(suite_ids=[], request_source=RequestSource.NIGHTLY,
                     requested_by="nightly-dab")
    session.add(req)
    session.commit()
    assert suite_safety.is_suite_referenced_by_nightly_job(session, suite.id) is True


def test_nightly_detection_no_nightly_runs(session):
    suite = Suite(name="un-scheduled", description="")
    session.add(suite)
    session.commit()
    session.refresh(suite)
    assert suite_safety.is_suite_referenced_by_nightly_job(session, suite.id) is False


def test_nightly_detection_api_run_does_not_count(session):
    suite = Suite(name="api-only", description="")
    session.add(suite)
    session.commit()
    session.refresh(suite)
    req = RunRequest(suite_ids=[suite.id], request_source=RequestSource.API,
                     requested_by="api-user")
    session.add(req)
    session.commit()
    assert suite_safety.is_suite_referenced_by_nightly_job(session, suite.id) is False


def test_nightly_detection_different_suite_id_not_matched(session):
    suite_a = Suite(name="suite-a", description="")
    suite_b = Suite(name="suite-b", description="")
    session.add(suite_a)
    session.add(suite_b)
    session.commit()
    session.refresh(suite_a)
    session.refresh(suite_b)
    req = RunRequest(suite_ids=[suite_a.id], request_source=RequestSource.NIGHTLY,
                     requested_by="nightly-dab")
    session.add(req)
    session.commit()
    assert suite_safety.is_suite_referenced_by_nightly_job(session, suite_a.id) is True
    assert suite_safety.is_suite_referenced_by_nightly_job(session, suite_b.id) is False
