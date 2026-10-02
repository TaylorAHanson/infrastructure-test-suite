"""seed_all() seam tests (issue #92): exemplars are tagged tutorial/example, the
worked-example + onboarding libraries still seed, the real catalog is scripts
(see test_seeded_catalog.py), and seeding stays idempotent.

Mirrors the seam decision on spec #91: assert the observable shape of the
catalog seed_all() produces, not seed-internal structure.
"""
from __future__ import annotations

from sqlmodel import select

from app.models import Check, Suite, Test, WorkedExample
from app.services.seed import (
    CHECK_SPECS,
    ONBOARDING_EXAMPLES,
    TUTORIAL_TAGS,
    WORKED_EXAMPLES,
    seed_all,
)

# The structured tutorial Tests seed_all() builds (issue #92).
_TUTORIAL_TEST_NAMES = {
    "SQL Warehouse validation", "Unity Catalog access", "Egress connectivity",
    "Cluster policy enforcement", "Platform self-check",
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


def test_exemplar_tests_carry_the_tutorial_tag_and_stay_structured(seeded_session):
    tests = seeded_session.exec(select(Test)).all()
    exemplars = [t for t in tests if t.name in _TUTORIAL_TEST_NAMES]
    assert len(exemplars) == len(_TUTORIAL_TEST_NAMES), "expected every tutorial Test to be seeded"
    for test in exemplars:
        assert set(TUTORIAL_TAGS) <= set(test.tags or []), (
            f"{test.name!r} is missing the tutorial/example tag: {test.tags}")
        assert not test.script


def test_every_non_tutorial_test_is_a_script(seeded_session):
    for test in seeded_session.exec(select(Test)).all():
        if test.name not in _TUTORIAL_TEST_NAMES:
            assert test.script, f"{test.name!r} should be seeded as a script"


def test_worked_example_library_still_seeds(seeded_session):
    examples = seeded_session.exec(select(WorkedExample)).all()
    assert len(examples) == len(WORKED_EXAMPLES) + len(ONBOARDING_EXAMPLES)


def test_onboarding_examples_still_present_and_tagged(seeded_session):
    examples = seeded_session.exec(select(WorkedExample)).all()
    onboarding = [e for e in examples if "onboarding" in (e.tags or [])]
    assert len(onboarding) == len(ONBOARDING_EXAMPLES)


def test_full_regression_holds_every_seeded_test(seeded_session):
    from app.models import SuiteMember

    suite = seeded_session.exec(select(Suite).where(Suite.name == "Full Regression")).one()
    members = seeded_session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()
    assert len(members) == len(seeded_session.exec(select(Test)).all())


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
