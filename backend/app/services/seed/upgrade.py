"""Bring an already-seeded database up to the current catalog.

``seed_all()`` builds the catalog only into an empty database, so an install
seeded by an older release keeps the old Tests forever. This retires the Tests
and Suites earlier releases seeded and adds whatever the current catalog has
that the database doesn't. It's idempotent: a database that's current is left
alone.

Retiring is in place, never a delete: run history points at the Test and its
Checks, so a retired Test is disabled, renamed with a ``(retired)`` suffix,
tagged ``retired`` and pulled out of every Suite. Tests a person created are
never touched. A retired Test is recognised by an exact seeded name while it's
still structured, or by a structured Test holding a name the current catalog
now gives to a script Test.
"""
from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.models import Check, Suite, SuiteMember, Test, TestCheck
from app.services.seed.build import make_check, make_suite, make_test
from app.services.seed.contribution import Contribution, SuiteSpec

logger = logging.getLogger(__name__)

RETIRED_TAG = "retired"

#: Structured Tests earlier releases seeded that the current catalog replaces.
RETIRED_TESTS = frozenset({
    "Consumer security posture",
    "Governance / Unity Catalog posture",
    "Identity & Access posture (read-only)",
    "Identity & Access posture (advisory)",
    "Processing compute posture",
    "Producer ingestion posture",
    "Job create/run/delete smoke",
    "SSO enforcement",
    "User isolation enforcement",
    "Exfiltration controls",
    "Token hygiene",
    "Workspace-admin count (advisory)",
    "Producer ingestion access",
    "Processing layer access",
})

#: Suites earlier releases seeded; their members are retired or regrouped.
RETIRED_SUITES = frozenset({
    "Consumer Suite",
    "Governance / Unity Catalog Suite",
    "Identity & Access Suite",
    "Processing Suite",
    "Producer Suite",
    "Sandbox Functional",
    "Security Posture Suite",
})

#: Suites whose membership the catalog owns outright, so it's reset on upgrade.
_OWNED_SUITES = frozenset({"CI Gate"})


def is_retired(test: Test) -> bool:
    return RETIRED_TAG in (test.tags or [])


def _retire_test(session: Session, test: Test, keep_check_ids: set[str]) -> None:
    test.enabled = False
    test.tags = [*(test.tags or []), RETIRED_TAG]
    if not test.name.endswith(" (retired)"):
        test.name = f"{test.name} (retired)"
    session.add(test)
    for member in session.exec(select(SuiteMember).where(SuiteMember.test_id == test.id)).all():
        session.delete(member)
    for link in session.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all():
        check = session.get(Check, link.check_id)
        if check is not None and check.id not in keep_check_ids:
            check.enabled = False
            session.add(check)


def _set_members(session: Session, suite: Suite, tests: list[Test]) -> None:
    for member in session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all():
        session.delete(member)
    session.flush()
    for i, test in enumerate(tests):
        session.add(SuiteMember(suite_id=suite.id, test_id=test.id, sort_order=i))


def _add_missing_members(session: Session, suite: Suite, tests: list[Test]) -> int:
    present = {m.test_id for m in session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()}
    order = len(present)
    added = 0
    for test in tests:
        if test.id not in present:
            session.add(SuiteMember(suite_id=suite.id, test_id=test.id, sort_order=order))
            order, added = order + 1, added + 1
    return added


def upgrade_catalog(session: Session, contributions: list[Contribution]) -> dict:
    """Retire superseded seeded Tests/Suites and add what the catalog is missing."""
    specs = {key: spec for c in contributions for key, spec in c.tests.items()}
    script_names = {spec.name for spec in specs.values() if spec.script}
    live = [t for t in session.exec(select(Test)).all() if not is_retired(t)]

    stale = [t for t in live if not t.script and (t.name in RETIRED_TESTS or t.name in script_names)]
    stale_ids = {t.id for t in stale}
    keep_check_ids = {link.check_id for link in session.exec(select(TestCheck)).all()
                      if link.test_id not in stale_ids}
    for test in stale:
        _retire_test(session, test, keep_check_ids)

    retired_suites = 0
    for suite in session.exec(select(Suite)).all():
        if suite.name in RETIRED_SUITES:
            for member in session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all():
                session.delete(member)
            session.delete(suite)
            retired_suites += 1
    session.commit()

    by_name = {t.name: t for t in session.exec(select(Test)).all() if not is_retired(t)}
    checks_by_name = {c.display_name: c for c in session.exec(select(Check)).all() if c.enabled}
    check_specs = {key: spec for c in contributions for key, spec in c.check_specs}
    check_tags = {key: c.check_tags for c in contributions for key, _ in c.check_specs}

    tests: dict[str, Test] = {}
    added_tests = 0
    for key, spec in specs.items():
        existing = by_name.get(spec.name)
        if existing is not None:
            tests[key] = existing
            continue
        checks: dict[str, Check] = {}
        for check_key, _ in spec.members:
            check = checks_by_name.get(check_specs[check_key]["display_name"])
            if check is None:
                check = make_check(check_specs[check_key], tags=check_tags.get(check_key) or None)
                session.add(check)
                session.commit()
                session.refresh(check)
                checks_by_name[check.display_name] = check
            checks[check_key] = check
        tests[key] = make_test(session, checks, spec)
        added_tests += 1

    suites = {s.name: s for s in session.exec(select(Suite)).all()}
    suite_specs = [s for c in contributions for s in c.suites]
    suite_specs.append(SuiteSpec("Full Regression", "Every seeded Test.", list(specs)))
    added_suites = 0
    for suite_spec in suite_specs:
        members = [tests[k] for k in suite_spec.test_keys]
        suite = suites.get(suite_spec.name)
        if suite is None:
            make_suite(session, tests, suite_spec)
            added_suites += 1
        elif suite_spec.name in _OWNED_SUITES:
            suite.description = suite_spec.description
            suite.deterministic_only = suite_spec.deterministic_only
            session.add(suite)
            _set_members(session, suite, members)
        else:
            _add_missing_members(session, suite, members)
    session.commit()

    summary = {"retired_tests": len(stale), "retired_suites": retired_suites,
               "added_tests": added_tests, "added_suites": added_suites}
    if any(summary.values()):
        from app.services.audit import record_audit

        record_audit(session, actor="seed", actor_kind="system", action="catalog.upgrade",
                     entity_type="catalog", summary=(
                         f"Catalog upgrade: retired {len(stale)} Tests and {retired_suites} Suites; "
                         f"added {added_tests} Tests and {added_suites} Suites."),
                     after=summary)
        logger.info("Catalog upgraded: %s", summary)
    return summary
