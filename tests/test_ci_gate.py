"""seed_all() seam tests for the reworked CI Gate Suite (issue #98).

Mirrors the seam decision on spec #91 (see test_seed.py): assert the
observable shape ``seed_all()`` produces — which Checks the CI Gate Suite
transitively contains — not seed-internal structure. The load-bearing claims
from the acceptance criteria: the CI Gate's membership is exactly the
deterministic, read-only posture Checks from the Security Posture, Identity &
Access, and Governance/UC Suites (ADR-0008 deterministic-only gate); no
agentic/advisory Check is a member; no mutating Check (e.g. the job-smoke) is
a member; and the Suite itself stays ``deterministic_only=True``.
"""
from __future__ import annotations

from sqlmodel import select

from app.core.enums import Evaluator
from app.models import Check, Suite, SuiteMember, TestCheck

# The exact deterministic, read-only Check display names the CI Gate must
# contain: the objective settings reads from Security Posture (#93), the
# three deterministic Identity & Access reads (#94), and every Governance/UC
# read including the grant-denied counterfactual (#95). Scoped by name on
# purpose — each source Suite also carries an agentic/advisory Check the gate
# must exclude, so deriving "the gate's contents" from a source Suite's full
# Check set would pull those in too.
_CI_GATE_CHECK_NAMES = {
    # Security Posture (#93)
    "SSO enabled",
    "No unisolated ('No Isolation Shared') clusters",
    "Exfiltration controls disabled",
    "Personal access tokens are expiry-bounded",
    # Identity & Access (#94)
    "Effective identity resolves (who-am-I)",
    "SCIM-provisioned groups exist",
    "IP access list is configured and enabled",
    # Governance / Unity Catalog (#95)
    "Unity Catalog metastore is assigned",
    "Production catalog is bound ISOLATED",
    "External locations enumerate without error",
    "Storage credentials enumerate without error",
    "Effective grant read is allowed",
    "Ungranted read is denied (fails closed)",
}

# Every agentic/advisory Check seeded anywhere in the catalog — none of these
# may ever be a CI Gate member.
_ADVISORY_CHECK_NAMES = {
    "Workspace-admin count is sane",
    "Service principals are not blanket workspace-admins",
}

# The catalog's one mutating Check (#97) — must never be a CI Gate member.
_MUTATING_CHECK_NAME = "Job create/run/delete smoke"


def _ci_gate_suite(session) -> Suite:
    suite = session.exec(select(Suite).where(Suite.name == "CI Gate")).first()
    assert suite is not None, "expected a seeded 'CI Gate' Suite"
    return suite


def _ci_gate_checks(session) -> list[Check]:
    suite = _ci_gate_suite(session)
    test_ids = {
        m.test_id for m in session.exec(
            select(SuiteMember).where(SuiteMember.suite_id == suite.id)
        ).all()
    }
    check_ids = {
        tc.check_id for tc in session.exec(
            select(TestCheck).where(TestCheck.test_id.in_(test_ids))
        ).all()
    } if test_ids else set()
    return [session.get(Check, cid) for cid in check_ids]


def test_ci_gate_is_deterministic_only(seeded_session):
    suite = _ci_gate_suite(seeded_session)
    assert suite.deterministic_only is True


def test_ci_gate_contains_exactly_the_deterministic_posture_checks(seeded_session):
    checks = _ci_gate_checks(seeded_session)
    names = {c.display_name for c in checks}
    assert names == _CI_GATE_CHECK_NAMES


def test_ci_gate_checks_are_all_deterministic_and_read_only(seeded_session):
    checks = _ci_gate_checks(seeded_session)
    assert checks, "CI Gate must not be empty"
    for check in checks:
        assert check.evaluator == Evaluator.DETERMINISTIC, (
            f"{check.display_name!r} is not deterministic and must not gate CI")
        assert check.compilable is True
        assert check.operation.get("verb") == "GET", (
            f"{check.display_name!r} is not a read (verb={check.operation.get('verb')})")


def test_ci_gate_excludes_every_agentic_check(seeded_session):
    names = {c.display_name for c in _ci_gate_checks(seeded_session)}
    assert not (names & _ADVISORY_CHECK_NAMES), (
        "an agentic/advisory Check leaked into the deterministic-only CI Gate")


def test_ci_gate_excludes_the_mutating_job_smoke_check(seeded_session):
    names = {c.display_name for c in _ci_gate_checks(seeded_session)}
    assert _MUTATING_CHECK_NAME not in names


def test_ci_gate_excludes_tutorial_exemplars(seeded_session):
    """The old CI Gate composed from tutorial Tests (``sql``, ``policy``);
    the reworked gate is real posture catalog content only."""
    checks = _ci_gate_checks(seeded_session)
    tutorial_names = {"SQL warehouse healthcheck", "Cluster create is allowed",
                      "Non-compliant cluster create is denied"}
    names = {c.display_name for c in checks}
    assert not (names & tutorial_names)
