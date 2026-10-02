"""seed_all() seam tests for the CI Gate Suite (issue #98).

The gate is composed from every domain's ``ci_gate_tests`` opt-in, re-derived
before it's trusted: a script Test joins only if its analysis is read-only with
no advisory checks. Today that is the quick health tier.
"""
from __future__ import annotations

from sqlmodel import select

from app.core.enums import Evaluator, RiskTier
from app.models import Check, Suite, SuiteMember, TestCheck
from app.services.seed.contribution import Contribution, TestSpec
from app.services.seed.domains.ci_gate import compose_contribution

_CI_GATE_CHECK_NAMES = {
    "the target identity authenticates",
    "the expected metastore is assigned",
    "the canary catalog is visible",
    "a SQL warehouse runs a statement",
    "the canary table returns its known row",
    "the canary volume serves its known file",
    "the ungranted canary table is refused",
}


def _ci_gate_suite(session) -> Suite:
    suite = session.exec(select(Suite).where(Suite.name == "CI Gate")).first()
    assert suite is not None, "expected a seeded 'CI Gate' Suite"
    return suite


def _ci_gate_checks(session) -> list[Check]:
    suite = _ci_gate_suite(session)
    test_ids = {m.test_id for m in session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()}
    check_ids = {tc.check_id for tc in session.exec(
        select(TestCheck).where(TestCheck.test_id.in_(test_ids))).all()} if test_ids else set()
    return [session.get(Check, cid) for cid in check_ids]


def test_ci_gate_contains_exactly_the_quick_health_checks(seeded_session):
    assert {c.display_name for c in _ci_gate_checks(seeded_session)} == _CI_GATE_CHECK_NAMES


def test_ci_gate_checks_are_all_deterministic_read_only_and_gating(seeded_session):
    for check in _ci_gate_checks(seeded_session):
        assert check.evaluator == Evaluator.DETERMINISTIC, check.display_name
        assert check.risk_tier == RiskTier.R0.value, check.display_name
        assert not (check.hints or {}).get("advisory"), check.display_name


def test_read_only_tests_that_did_not_volunteer_stay_out(seeded_session):
    connectivity = {"a SQL warehouse returns the healthcheck row", "the identity can see at least one catalog",
                    "an external HTTPS endpoint answers without an error status"}
    assert not ({c.display_name for c in _ci_gate_checks(seeded_session)} & connectivity)


def _gate_keys(*tests: tuple[str, TestSpec]) -> list[str]:
    contribution = Contribution(tests=dict(tests), ci_gate_tests=[k for k, _ in tests])
    (suite,) = compose_contribution([contribution]).suites
    return suite.test_keys


def test_a_mutating_script_that_volunteers_is_kept_out():
    script = 'with check("writes"):\n    sql("CREATE TABLE x.y.z (a INT)")\n    assert True\n'
    assert _gate_keys(("mut", TestSpec("m", "", script=script))) == []


def test_a_script_with_an_advisory_check_that_volunteers_is_kept_out():
    script = 'with check("judged", advisory=True):\n    assert w.current_user.me()\n'
    assert _gate_keys(("adv", TestSpec("a", "", script=script))) == []


def test_a_read_only_script_that_volunteers_is_admitted():
    script = 'with check("reads"):\n    assert w.current_user.me()\n'
    assert _gate_keys(("ok", TestSpec("o", "", script=script))) == ["ok"]
