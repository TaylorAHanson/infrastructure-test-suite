"""seed_all() seam tests (issues #92, #94): exemplars are tagged tutorial/
example, the worked-example + onboarding libraries still seed, seeding stays
idempotent, and the real posture catalog's Identity & Access slice compiles
with the guarantees Spec #91 promises (read-only/R0/deterministic for the
objective reads, agentic/advisory for the least-privilege judgment read).

Mirrors the seam decision on spec #91: assert the observable shape of the
catalog seed_all() produces, not seed-internal structure.
"""
from __future__ import annotations

from sqlmodel import select

from app.core.enums import Evaluator, RiskTier
from app.models import Check, Suite, SuiteMember, Test, TestCheck, WorkedExample
from app.services.seed import (
    CATALOG_CHECK_SPECS,
    CHECK_SPECS,
    ONBOARDING_EXAMPLES,
    TUTORIAL_TAGS,
    WORKED_EXAMPLES,
    seed_all,
)

# The three deterministic, read-only (R0) reads in the Identity & Access
# slice (issue #94) — the least-privilege judgment Check is asserted
# separately below since it must compile the *opposite* way.
_IDENTITY_ACCESS_DETERMINISTIC_CHECKS = {
    "Effective identity resolves (who-am-I)",
    "SCIM-provisioned groups exist",
    "IP access list is configured and enabled",
}
_IDENTITY_ACCESS_ADVISORY_CHECK = "Service principals are not blanket workspace-admins"

# The Test names seed_all() currently builds with tutorial=True (issue #92).
# Scoped on purpose: a future Spec #91 slice adds real-catalog Tests to the
# same seeded set *without* this tag, so asserting "every Test" here would
# break the very seam this ticket establishes.
_TUTORIAL_TEST_NAMES = {
    "SQL Warehouse validation", "Unity Catalog access", "Egress connectivity",
    "Cluster policy enforcement", "Platform self-check",
    "Producer ingestion access", "Processing layer access",
}


def test_exemplar_checks_carry_the_tutorial_tag(seeded_session):
    exemplar_names = {spec["display_name"] for _, spec in CHECK_SPECS}
    checks = seeded_session.exec(select(Check)).all()
    exemplars = [c for c in checks if c.display_name in exemplar_names]
    assert len(exemplars) == len(CHECK_SPECS), "expected every CHECK_SPECS exemplar to be seeded"
    for check in exemplars:
        check_tags = set(check.hints.get("tags") or [])
        assert set(TUTORIAL_TAGS) <= check_tags, (
            f"{check.display_name!r} is missing the tutorial/example tag: {check.hints}")


def test_exemplar_tests_carry_the_tutorial_tag(seeded_session):
    tests = seeded_session.exec(select(Test)).all()
    exemplars = [t for t in tests if t.name in _TUTORIAL_TEST_NAMES]
    assert len(exemplars) == len(_TUTORIAL_TEST_NAMES), "expected every tutorial Test to be seeded"
    for test in exemplars:
        assert set(TUTORIAL_TAGS) <= set(test.tags or []), (
            f"{test.name!r} is missing the tutorial/example tag: {test.tags}")


def test_worked_example_library_still_seeds(seeded_session):
    examples = seeded_session.exec(select(WorkedExample)).all()
    assert len(examples) == len(WORKED_EXAMPLES) + len(ONBOARDING_EXAMPLES)


def test_onboarding_examples_still_present_and_tagged(seeded_session):
    examples = seeded_session.exec(select(WorkedExample)).all()
    onboarding = [e for e in examples if "onboarding" in (e.tags or [])]
    assert len(onboarding) == len(ONBOARDING_EXAMPLES)


def test_identity_access_suite_is_seeded_and_nonempty(seeded_session):
    suites = {s.name: s for s in seeded_session.exec(select(Suite)).all()}
    suite = suites.get("Identity & Access Suite")
    assert suite is not None, "expected a seeded 'Identity & Access Suite'"
    members = seeded_session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite.id)
    ).all()
    assert members, "Identity & Access Suite must not be empty"
    # It carries the advisory SP check, so it can never be a deterministic-only
    # (CI-gateable) Suite (ADR-0008).
    assert suite.deterministic_only is False


def test_identity_access_readonly_checks_are_r0_deterministic(seeded_session):
    checks = {c.display_name: c for c in seeded_session.exec(select(Check)).all()}
    assert len(_IDENTITY_ACCESS_DETERMINISTIC_CHECKS) == sum(
        1 for _, spec in CATALOG_CHECK_SPECS
        if spec["display_name"] in _IDENTITY_ACCESS_DETERMINISTIC_CHECKS
    ), "expected every deterministic Identity & Access Check to be declared in CATALOG_CHECK_SPECS"
    for name in _IDENTITY_ACCESS_DETERMINISTIC_CHECKS:
        check = checks[name]
        assert check.operation.get("verb") == "GET", f"{name!r} must be a read (verb=GET)"
        assert check.risk_tier == RiskTier.R0.value, f"{name!r} must derive to R0"
        assert check.evaluator == Evaluator.DETERMINISTIC, f"{name!r} must compile deterministic"
        assert check.compilable is True


def test_service_principal_least_privilege_check_is_agentic_advisory(seeded_session):
    checks = {c.display_name: c for c in seeded_session.exec(select(Check)).all()}
    check = checks[_IDENTITY_ACCESS_ADVISORY_CHECK]
    assert check.evaluator == Evaluator.AGENTIC
    assert check.compilable is False
    # Advisory only — must never appear in a deterministic-only (CI Gate) Suite.
    for suite in seeded_session.exec(select(Suite).where(Suite.deterministic_only == True)).all():  # noqa: E712
        test_ids = {
            m.test_id for m in seeded_session.exec(
                select(SuiteMember).where(SuiteMember.suite_id == suite.id)
            ).all()
        }
        check_ids = {
            tc.check_id for tc in seeded_session.exec(
                select(TestCheck).where(TestCheck.test_id.in_(test_ids))
            ).all()
        } if test_ids else set()
        assert check.id not in check_ids, f"advisory Check leaked into deterministic-only {suite.name!r}"


def test_seeding_is_idempotent(seeded_session):
    check_count = len(seeded_session.exec(select(Check)).all())
    test_count = len(seeded_session.exec(select(Test)).all())
    suite_count = len(seeded_session.exec(select(Suite)).all())
    example_count = len(seeded_session.exec(select(WorkedExample)).all())

    seed_all(seeded_session)  # second seed: must be a no-op

    assert len(seeded_session.exec(select(Check)).all()) == check_count
    assert len(seeded_session.exec(select(Test)).all()) == test_count
    assert len(seeded_session.exec(select(Suite)).all()) == suite_count
    assert len(seeded_session.exec(select(WorkedExample)).all()) == example_count
