"""seed_all() seam tests: the bundled catalog is script Tests only, every bundled
Test is seeded, nothing from the structured era (tutorial Tests, worked
examples) is, and seeding stays idempotent.

Mirrors the seam decision on spec #91: assert the observable shape of the
catalog seed_all() produces, not seed-internal structure.
"""
from __future__ import annotations

from sqlalchemy import inspect
from sqlmodel import select

from app.models import AuditEvent, Check, EntityVersion, Suite, SuiteMember, Test, TestCheck
from app.services.seed import seed_all
from app.services.seed.registry import discover_contributions


def test_every_bundled_test_is_seeded_as_a_script(seeded_session):
    bundled = {spec.name for c in discover_contributions() for spec in c.tests.values()}
    tests = seeded_session.exec(select(Test)).all()
    assert {t.name for t in tests} == bundled
    assert all(t.script for t in tests)


def test_every_seeded_check_is_a_script_check_its_test_lists(seeded_session):
    checks = seeded_session.exec(select(Check)).all()
    assert checks and {c.handler_key for c in checks} == {"script"}
    assert {c.id for c in checks} == {link.check_id for link in seeded_session.exec(select(TestCheck)).all()}


def test_nothing_from_the_structured_era_is_seeded(seeded_session):
    names = {t.name for t in seeded_session.exec(select(Test)).all()}
    assert not names & {"Cluster policy enforcement", "Platform self-check"}
    for test in seeded_session.exec(select(Test)).all():
        assert not {"tutorial", "example"} & set(test.tags or []), test.name
    assert not inspect(seeded_session.connection()).has_table("worked_examples")


def test_full_regression_holds_every_seeded_test(seeded_session):
    suite = seeded_session.exec(select(Suite).where(Suite.name == "Full Regression")).one()
    members = seeded_session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()
    assert {m.test_id for m in members} == {t.id for t in seeded_session.exec(select(Test)).all()}


def test_seeding_is_idempotent(seeded_session):
    models = (Check, Test, TestCheck, Suite, SuiteMember, EntityVersion, AuditEvent)
    counts = [len(seeded_session.exec(select(m)).all()) for m in models]

    seed_all(seeded_session)  # second seed: must be a no-op

    assert [len(seeded_session.exec(select(m)).all()) for m in models] == counts
