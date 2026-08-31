"""seed_all() seam tests for the Governance / Unity Catalog Suite (issue #95).

Mirrors the seam decision on spec #91 (see test_seed.py): assert the
observable shape ``seed_all()`` produces — the Suite, its Checks' compiled
posture, and the counterfactual wiring — not seed-internal structure. One
test additionally exercises each seeded Check's operation through the real
``databricks_sdk`` handler in demo mode, proving the Suite (including the
deny path) is runnable without a workspace.
"""
from __future__ import annotations

from sqlmodel import select

from app.core.enums import Depth, Evaluator, Expectation, RiskTier, StageStatus
from app.core.operations import Operation
from app.handlers.base import HandlerContext
from app.handlers.builtin.generic import DatabricksSdkHandler
from app.models import Check, Suite, SuiteMember, Test, TestCheck
from app.services.assertion_compiler import evaluate_predicate
from app.services.seed import CATALOG_CHECK_SPECS

_GOVERNANCE_READ_KEYS = {
    "gov_metastore_assigned", "gov_catalog_isolated",
    "gov_external_locations", "gov_storage_credentials",
}
_REACHABILITY_KEYS = {"gov_external_locations", "gov_storage_credentials"}


def _governance_checks(session) -> dict[str, Check]:
    names_by_key = {k: spec["display_name"] for k, spec in CATALOG_CHECK_SPECS if k.startswith("gov_")}
    checks = session.exec(select(Check)).all()
    by_name = {c.display_name: c for c in checks}
    return {key: by_name[name] for key, name in names_by_key.items()}


def _governance_test(session) -> Test:
    suite = next(s for s in session.exec(select(Suite)).all()
                if s.name == "Governance / Unity Catalog Suite")
    member = session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).first()
    return session.get(Test, member.test_id)


def test_suite_is_seeded_and_non_empty(seeded_session):
    suite = next((s for s in seeded_session.exec(select(Suite)).all()
                 if s.name == "Governance / Unity Catalog Suite"), None)
    assert suite is not None, "Governance / Unity Catalog Suite was not seeded"

    members = seeded_session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()
    assert len(members) > 0, "Governance / Unity Catalog Suite has no Tests"

    test_checks = seeded_session.exec(
        select(TestCheck).where(TestCheck.test_id == members[0].test_id)).all()
    assert len(test_checks) > 0, "the Governance Test has no Checks"


def test_four_governance_reads_are_readonly_r0_deterministic(seeded_session):
    checks = _governance_checks(seeded_session)
    assert set(checks) >= _GOVERNANCE_READ_KEYS

    for key in _GOVERNANCE_READ_KEYS:
        check = checks[key]
        assert check.operation.get("verb") == "GET", f"{key} is not a read (verb={check.operation.get('verb')})"
        assert check.risk_tier == RiskTier.R0.value, f"{key} is not R0 (got {check.risk_tier})"
        assert check.evaluator == Evaluator.DETERMINISTIC, f"{key} did not compile deterministic"
        assert check.compilable is True


def test_reachability_style_assertions_never_require_non_empty(seeded_session):
    """Storage-credentials/external-locations must not false-fail a clean workspace."""
    checks = _governance_checks(seeded_session)
    for key in _REACHABILITY_KEYS:
        check = checks[key]
        assert "without error" in check.assertion.lower(), (
            f"{key} should use a reachability-style ('returns without error') assertion")
        # A bare success predicate, never a non-empty/row_count threshold —
        # an empty list on a clean workspace must still pass.
        assert check.compiled_predicate.get("kind") == "ok", (
            f"{key} must not assert non-emptiness (predicate={check.compiled_predicate})")


def test_grant_denied_counterfactual_is_wired(seeded_session):
    checks = _governance_checks(seeded_session)
    allowed = checks["gov_grant_read_allowed"]
    denied = checks["gov_grant_read_denied"]

    governance_test = _governance_test(seeded_session)
    test_checks = seeded_session.exec(
        select(TestCheck).where(TestCheck.test_id == governance_test.id)).all()
    cf_member = next(tc for tc in test_checks if tc.check_id == denied.id)

    assert cf_member.kind == "counterfactual"
    assert cf_member.counterpart_check_id == allowed.id, "counterfactual is not resolved to its counterpart"

    assert denied.expectation == Expectation.DENY
    assert denied.evaluator == Evaluator.DETERMINISTIC
    assert denied.compiled_predicate == {"kind": "denied"}
    assert denied.risk_tier == RiskTier.R0.value  # a read, even when denied


def test_all_governance_checks_run_in_demo_mode_without_a_workspace(seeded_session):
    """Every governance Check — including the deny path — is demo-runnable."""
    governance_test = _governance_test(seeded_session)
    test_checks = seeded_session.exec(
        select(TestCheck).where(TestCheck.test_id == governance_test.id)
        .order_by(TestCheck.sort_order)).all()
    assert len(test_checks) == 6

    for tc in test_checks:
        check = seeded_session.get(Check, tc.check_id)
        ctx = HandlerContext(
            run_id="demo-governance",
            operation=Operation.from_dict(check.operation),
            depth=Depth.FULL,
            params=dict(check.operation.get("params", {})),
            demo_mode=True,
        )
        steps = DatabricksSdkHandler().run(ctx)
        evidence: dict = {}
        for step in steps:
            evidence.update(step.evidence)
        operation_succeeded = not any(s.failed for s in steps)
        verdict = evaluate_predicate(check.compiled_predicate, evidence, operation_succeeded)
        assert verdict is True, f"{check.display_name} did not pass in demo mode"

        if tc.kind == "counterfactual":
            authorize = next(s for s in steps if s.stage == "AUTHORIZE")
            assert authorize.outcome == StageStatus.FAIL
            assert authorize.failure_bucket == "PERMISSION_DENIED"
