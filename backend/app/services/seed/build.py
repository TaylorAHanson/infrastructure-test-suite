"""Turn Contribution specs into ORM rows.

Cross-module key resolution (#106) lives here: ``checks``/``tests`` are the
dicts merged across *every* discovered domain, so a Test's member, a Suite's
test_key, or a counterfactual's counterpart may point at a Check/Test minted
by a domain module other than the one declaring the reference.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from app.models import Check, Suite, SuiteMember, Test, TestCheck
from app.schemas.schemas import CheckCreate
from app.services.authoring import build_check_fields
from app.services.seed.contribution import SuiteSpec, TestSpec


def make_check(spec: dict, *, tags: list[str] | None = None) -> Check:
    """Build a Check from a spec, optionally stamping ``hints["tags"]``.

    Check has no dedicated tags column (unlike Test/Suite); ``hints`` is the
    existing free-form grounding-metadata field, so tutorial/example tagging
    rides there rather than adding a migration for this prefactor.
    """
    if tags:
        spec = {**spec, "hints": {**spec.get("hints", {}), "tags": tags}}
    return Check(**build_check_fields(CheckCreate(**spec)))


def make_test(session: Session, checks: dict[str, Check], spec: TestSpec) -> Test:
    """Build a Test + its TestCheck members, wiring counterfactual counterparts."""
    test = Test(name=spec.name, description=spec.description, tags=spec.tags)
    session.add(test)
    session.commit()
    session.refresh(test)
    # A counterfactual's counterpart is the nearest preceding assertion-kind
    # member in this same members list — the positive Check it negates.
    last_assertion_key: Optional[str] = None
    for i, (check_key, kind) in enumerate(spec.members):
        counterpart = checks[last_assertion_key].id if kind == "counterfactual" and last_assertion_key else None
        session.add(TestCheck(test_id=test.id, check_id=checks[check_key].id, sort_order=i,
                              kind=kind, counterpart_check_id=counterpart))
        if kind == "assertion":
            last_assertion_key = check_key
    session.commit()
    return test


def make_suite(session: Session, tests: dict[str, Test], spec: SuiteSpec) -> Suite:
    """Build a Suite + its SuiteMember rows. CI Gate is deterministic-only (ADR-0008)."""
    suite = Suite(name=spec.name, description=spec.description,
                  tags=spec.tags or ["seeded"], deterministic_only=spec.deterministic_only)
    session.add(suite)
    session.commit()
    session.refresh(suite)
    for i, tk in enumerate(spec.test_keys):
        session.add(SuiteMember(suite_id=suite.id, test_id=tests[tk].id, sort_order=i))
    session.commit()
    return suite
