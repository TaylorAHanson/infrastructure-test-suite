"""seed_all() seam tests for the Security Posture Suite (issue #93).

Mirrors the seam decision on spec #91 (see test_seed.py): assert the
observable shape of the catalog seed_all() produces — the Suite/Tests/Checks
an operator would see — not seed-internal structure. The load-bearing claims
from the acceptance criteria: the Suite exists and is non-empty, the four
settings Checks are read-only/R0 and compile deterministic, the admin-count
Check compiles agentic/advisory, and none of this carries TUTORIAL_TAGS (it's
real catalog content, not the tutorial library).
"""
from __future__ import annotations

from sqlmodel import select

from app.core.enums import Evaluator, RiskTier
from app.models import Check, Suite, SuiteMember, Test, TestCheck
from app.services.seed import TUTORIAL_TAGS

_DETERMINISTIC_CHECK_NAMES = {
    "SSO enabled",
    "No unisolated ('No Isolation Shared') clusters",
    "Exfiltration controls disabled",
    "Personal access tokens are expiry-bounded",
}
_ADVISORY_CHECK_NAME = "Workspace-admin count is sane"

# The full Security Posture catalog (issue #93). Scoped by name on purpose —
# CATALOG_CHECK_SPECS is a shared list other slices (e.g. issue #96) also
# append to, so deriving "the catalog" from that shared list would pull in
# Checks this Suite never claims to contain.
_SECURITY_POSTURE_CHECK_NAMES = _DETERMINISTIC_CHECK_NAMES | {_ADVISORY_CHECK_NAME}


def _security_posture_checks(session) -> list[Check]:
    checks = session.exec(select(Check)).all()
    return [c for c in checks if c.display_name in _SECURITY_POSTURE_CHECK_NAMES]


def test_security_posture_suite_exists_and_is_non_empty(seeded_session):
    suite = seeded_session.exec(select(Suite).where(Suite.name == "Security Posture Suite")).first()
    assert suite is not None
    members = seeded_session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()
    assert len(members) == 5


def test_settings_checks_are_read_only_r0_and_deterministic(seeded_session):
    checks = {c.display_name: c for c in _security_posture_checks(seeded_session)}
    assert _DETERMINISTIC_CHECK_NAMES <= set(checks)
    for name in _DETERMINISTIC_CHECK_NAMES:
        check = checks[name]
        assert check.operation["verb"] == "GET", f"{name!r} must be a read (GET)"
        assert check.risk_tier == RiskTier.R0.value, f"{name!r} must derive R0 from its GET verb"
        assert check.evaluator == Evaluator.DETERMINISTIC, f"{name!r} must compile deterministic"
        assert check.compilable is True


def test_admin_count_check_is_agentic_advisory(seeded_session):
    checks = {c.display_name: c for c in _security_posture_checks(seeded_session)}
    check = checks[_ADVISORY_CHECK_NAME]
    assert check.evaluator == Evaluator.AGENTIC
    assert check.compilable is False
    # Still a read (GET) — the never-mutate posture-battery guarantee holds
    # even for the judgement Check.
    assert check.operation["verb"] == "GET"
    assert check.risk_tier == RiskTier.R0.value


def test_security_posture_checks_are_not_tutorial_tagged(seeded_session):
    for check in _security_posture_checks(seeded_session):
        check_tags = set(check.hints.get("tags") or [])
        assert not (set(TUTORIAL_TAGS) & check_tags), (
            f"{check.display_name!r} is real catalog content and must not carry TUTORIAL_TAGS")


def test_security_posture_suite_contains_exactly_the_catalog_checks(seeded_session):
    suite = seeded_session.exec(select(Suite).where(Suite.name == "Security Posture Suite")).first()
    members = seeded_session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()
    test_ids = {m.test_id for m in members}
    check_ids: set[str] = set()
    for test_id in test_ids:
        test = seeded_session.get(Test, test_id)
        assert not (set(TUTORIAL_TAGS) & set(test.tags or [])), (
            f"{test.name!r} is real catalog content and must not carry TUTORIAL_TAGS")
        for tc in seeded_session.exec(select(TestCheck).where(TestCheck.test_id == test_id)).all():
            check_ids.add(tc.check_id)

    seeded_display_names = {seeded_session.get(Check, cid).display_name for cid in check_ids}
    assert seeded_display_names == _SECURITY_POSTURE_CHECK_NAMES
