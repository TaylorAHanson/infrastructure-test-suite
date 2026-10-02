"""Turn Contribution specs into ORM rows.

Cross-module key resolution (#106) lives here: ``tests`` is the dict merged
across *every* discovered domain, so a Suite's test_key may point at a Test
minted by a domain module other than the one declaring the Suite.
"""
from __future__ import annotations

from sqlmodel import Session

from app.models import Suite, SuiteMember, Test
from app.services.seed.contribution import SuiteSpec, TestSpec


def make_test(session: Session, spec: TestSpec) -> Test:
    """A script Test: its Check rows are synced from the script's declared checks."""
    from app.script_runtime.analysis import analyze
    from app.services.script_tests import sync_script_checks

    analysis = analyze(spec.script)
    if not analysis["ok"]:
        raise ValueError(f"seeded test {spec.name!r} has an invalid script: {analysis['errors']}")
    test = Test(name=spec.name, description=spec.description, tags=spec.tags, script=spec.script)
    session.add(test)
    session.flush()
    sync_script_checks(session, test, spec.script, analysis)
    session.commit()
    session.refresh(test)
    return test


def make_suite(session: Session, tests: dict[str, Test], spec: SuiteSpec) -> Suite:
    """Build a Suite + its SuiteMember rows."""
    suite = Suite(name=spec.name, description=spec.description, tags=spec.tags or ["seeded"])
    session.add(suite)
    session.commit()
    session.refresh(suite)
    for i, tk in enumerate(spec.test_keys):
        session.add(SuiteMember(suite_id=suite.id, test_id=tests[tk].id, sort_order=i))
    session.commit()
    return suite
