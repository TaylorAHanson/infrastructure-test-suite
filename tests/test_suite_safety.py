"""Validator-level tests for Suite composition safety (issue #63).

Mirrors the pattern in test_safety_gate.py / test_authoring_compiler.py:
tests the validator directly at the service layer, not only through the HTTP seam.
"""
from __future__ import annotations

from app.core.enums import Evaluator, RequestSource
from app.models import Check, RunRequest, Suite, SuiteMember, Test, TestCheck
from app.services import suite_safety


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_det_only_suite(session) -> Suite:
    suite = Suite(name="det-only-suite", description="", deterministic_only=True)
    session.add(suite)
    session.commit()
    session.refresh(suite)
    return suite


def _make_test_with_check(session, evaluator: Evaluator) -> Test:
    check = Check(
        display_name="chk",
        operation={"service": "jobs", "method": "list"},
        handler_key="databricks_sdk",
        assertion="works",
        evaluator=evaluator,
        compiled_predicate={},
    )
    session.add(check)
    session.commit()
    session.refresh(check)
    test = Test(name=f"t-{evaluator.value}", description="")
    session.add(test)
    session.commit()
    session.refresh(test)
    session.add(TestCheck(test_id=test.id, check_id=check.id, sort_order=0))
    session.commit()
    return test


# ---------------------------------------------------------------------------
# Deterministic-only violation detection
# ---------------------------------------------------------------------------
def test_no_violation_for_non_det_only_suite(session):
    suite = Suite(name="open-suite", description="", deterministic_only=False)
    session.add(suite)
    session.commit()
    session.refresh(suite)
    agentic_test = _make_test_with_check(session, Evaluator.AGENTIC)
    result = suite_safety.check_deterministic_violation(session, suite, [agentic_test.id])
    assert result is None  # not constrained — no flag set


def test_no_violation_adding_deterministic_test_to_det_only_suite(session):
    suite = _make_det_only_suite(session)
    det_test = _make_test_with_check(session, Evaluator.DETERMINISTIC)
    result = suite_safety.check_deterministic_violation(session, suite, [det_test.id])
    assert result is None


def test_violation_adding_agentic_test_to_det_only_suite(session):
    suite = _make_det_only_suite(session)
    agentic_test = _make_test_with_check(session, Evaluator.AGENTIC)
    result = suite_safety.check_deterministic_violation(session, suite, [agentic_test.id])
    assert result is not None
    assert "agentic" in result.lower()


def test_violation_toggling_det_only_true_with_agentic_member(session):
    suite = Suite(name="mixed-suite", description="", deterministic_only=False)
    session.add(suite)
    session.commit()
    session.refresh(suite)
    agentic_test = _make_test_with_check(session, Evaluator.AGENTIC)
    session.add(SuiteMember(suite_id=suite.id, test_id=agentic_test.id, sort_order=0))
    session.commit()
    result = suite_safety.check_deterministic_violation(
        session, suite, [agentic_test.id], proposed_deterministic_only=True)
    assert result is not None


def test_no_violation_toggling_det_only_false(session):
    suite = _make_det_only_suite(session)
    det_test = _make_test_with_check(session, Evaluator.DETERMINISTIC)
    result = suite_safety.check_deterministic_violation(
        session, suite, [det_test.id], proposed_deterministic_only=False)
    assert result is None  # loosening the constraint always succeeds


def test_violation_message_names_culprit_tests(session):
    suite = _make_det_only_suite(session)
    agentic_test = _make_test_with_check(session, Evaluator.AGENTIC)
    result = suite_safety.check_deterministic_violation(session, suite, [agentic_test.id])
    assert result is not None
    assert agentic_test.id in result


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
