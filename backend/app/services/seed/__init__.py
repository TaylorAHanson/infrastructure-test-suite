"""Seed data: the bundled Test/Suite catalog, so the app is useful on first boot.

Idempotent: it first deletes whatever the retired structured-test feature left
behind (:func:`~app.services.legacy_purge.purge_legacy`), then into an empty
database it builds the catalog and into a seeded one it runs
:func:`~app.services.seed.upgrade.upgrade_catalog`, which removes what earlier
releases seeded and adds what's missing. The catalog is composed from
**auto-discovered domain modules** under :mod:`app.services.seed.domains`
(#106): a domain module declares a
:class:`~app.services.seed.contribution.Contribution` of its own script Tests
and Suites, and :func:`app.services.seed.registry.discover_contributions` picks
it up with no edit to any shared file. ``seed_all()`` merges every
contribution into one catalog before building rows, so a Suite naming a Test
from another domain resolves against the merged whole.

Every domain — connectivity, the quick and compute health tiers, the security
posture battery and the CI Gate composed from them — owns its own module under
``.domains``; no domain's content lives in a shared structure here.
"""
from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.core.enums import SafetyClass
from app.models import Check, Suite, Target, Test
from app.services.legacy_purge import purge_legacy
from app.services.seed.build import make_suite, make_test
from app.services.seed.contribution import SuiteSpec
from app.services.seed.registry import discover_contributions
from app.services.seed.upgrade import SEED_ACTOR

logger = logging.getLogger(__name__)

__all__ = ["seed_all"]


def seed_all(session: Session, with_demo_targets: bool = True) -> None:
    """Seed the Test/Suite catalog (idempotent)."""
    # Branding is seeded independently of the catalog so an un-branded install
    # always has the finished default record (ADR-0013 §5), even once the catalog
    # already exists. Idempotent by the fixed singleton id.
    from app.services.branding import get_or_seed as seed_branding

    seed_branding(session)
    purge_legacy(session, actor=SEED_ACTOR)

    contributions = discover_contributions()
    # The purge can leave a seeded install without Tests or Checks, never without Suites.
    if any(session.exec(select(model)).first() for model in (Suite, Test, Check)):
        from app.services.seed.upgrade import upgrade_catalog

        upgrade_catalog(session, contributions)
        return

    logger.info("Seeding the Test/Suite catalog...")
    tests: dict[str, Test] = {}
    for contribution in contributions:
        for key, test_spec in contribution.tests.items():
            tests[key] = make_test(session, test_spec)

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
    logger.info("Seed complete: %d tests, %d suites.", len(tests), suite_count)
