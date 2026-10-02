"""Seed data: Check/Test/Suite exemplars so the app is useful on first boot.

Idempotent: into an empty database it builds the catalog; into a seeded one it
runs :func:`~app.services.seed.upgrade.upgrade_catalog`, which retires what
earlier releases seeded and adds what's missing. The catalog is composed
from **auto-discovered domain modules** under
:mod:`app.services.seed.domains` (#106): a domain module declares a
:class:`~app.services.seed.contribution.Contribution` of its own Checks/
Tests/Suites (and, for the worked-example library, WorkedExample rows), and
:func:`app.services.seed.registry.discover_contributions` picks it up with no
edit to any shared file. ``seed_all()`` merges every contribution into one
catalog before building rows, so a cross-module reference — a Test naming a
Check from another domain, a Suite naming a Test from another domain, a
counterfactual's counterpart — resolves against the merged whole.

Every real-catalog domain — the quick and compute health tiers and the
security posture battery, all script Tests — owns its own module under
``.domains``, alongside the tutorial
exemplars (:mod:`.domains.tutorial`) and the worked/onboarding example
libraries (:mod:`.domains.examples`) (#107). No domain's content lives in a
shared structure here; this file only re-exports the tutorial/example
libraries' constants for the tests that assert against them by name.
"""
from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.core.enums import Expectation, SafetyClass
from app.models import Check, Suite, Target, Test, WorkedExample
from app.services.seed.build import make_check, make_suite, make_test
from app.services.seed.contribution import Contribution, SuiteSpec
from app.services.seed.domains.examples import ONBOARDING_EXAMPLES, WORKED_EXAMPLES
from app.services.seed.domains.tutorial import CHECK_SPECS, TUTORIAL_TAGS
from app.services.seed.registry import discover_contributions

logger = logging.getLogger(__name__)

__all__ = [
    "CHECK_SPECS",
    "ONBOARDING_EXAMPLES",
    "TUTORIAL_TAGS",
    "WORKED_EXAMPLES",
    "seed_all",
]


def _seed_examples(session: Session, contributions: list[Contribution]) -> int:
    """Seed every domain's worked-example rows (idempotent within a fresh seed)."""
    if session.exec(select(WorkedExample)).first():
        return 0
    specs = [example for contribution in contributions for example in contribution.worked_examples]
    for spec in specs:
        session.add(WorkedExample(
            intent=spec["intent"], title=spec["title"], description=spec["intent"],
            operation=spec["operation"], handler_key=spec["handler_key"],
            assertion=spec["assertion"], expectation=Expectation.PASS,
            counterfactual=spec.get("counterfactual", {}), tags=spec["tags"],
            curated=True,
        ))
    session.commit()
    return len(specs)


def seed_all(session: Session, with_demo_targets: bool = True) -> None:
    """Seed Check/Test/Suite exemplars (idempotent)."""
    # Branding is seeded independently of the catalog so an un-branded install
    # always has the finished default record (ADR-0013 §5), even once the catalog
    # already exists. Idempotent by the fixed singleton id.
    from app.services.branding import get_or_seed as seed_branding

    seed_branding(session)

    contributions = discover_contributions()
    if session.exec(select(Check)).first():
        from app.services.seed.upgrade import upgrade_catalog

        upgrade_catalog(session, contributions)
        return


    _seed_examples(session, contributions)  # worked-example library (ADR-0006 §9)
    logger.info("Seeding Check/Test/Suite exemplars...")

    checks: dict[str, Check] = {}
    for contribution in contributions:
        for key, spec in contribution.check_specs:
            check = make_check(spec, tags=contribution.check_tags or None, seed_key=key)
            session.add(check)
            checks[key] = check
    session.commit()
    for check in checks.values():
        session.refresh(check)

    tests: dict[str, Test] = {}
    for contribution in contributions:
        for key, test_spec in contribution.tests.items():
            tests[key] = make_test(session, checks, test_spec)

    for contribution in contributions:
        for suite_spec in contribution.suites:
            make_suite(session, tests, suite_spec)

    # Every seeded Test, regardless of which domain minted it — cross-cutting,
    # so built generically here rather than owned by any one domain module.
    make_suite(session, tests, SuiteSpec("Full Regression", "Every seeded Test.", list(tests.keys())))

    if with_demo_targets:
        for t in [
            Target(name="aws-prod (read-only)", workspace_name="aws-prod-workspace",
                   safety_class=SafetyClass.READ_ONLY, tags={"environment": "prod"}),
            Target(name="sandbox-1", workspace_name="sandbox-workspace",
                   safety_class=SafetyClass.SANDBOX, tags={"environment": "sandbox"}),
            Target(name="prod-for-us-1", workspace_name="platform-workspace",
                   safety_class=SafetyClass.PROD_FOR_US, tags={"environment": "internal"}),
        ]:
            session.add(t)
        session.commit()

    suite_count = len(session.exec(select(Suite)).all())
    logger.info("Seed complete: %d checks, %d tests, %d suites.",
                len(checks), len(tests), suite_count)
