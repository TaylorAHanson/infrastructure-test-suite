"""Sandbox Functional Suite seam tests (issue #97): the catalog's one
mutating validation — create a trivial Job, run it, confirm output, delete it.

Mirrors the seam decision on spec #91: assert the observable shape of the
catalog ``seed_all()`` produces (the Suite, the effective sandbox-only risk
tier, and the cleanup declaration), not seed-internal structure. The demo-run
+ safety-gate behavior is exercised at the existing HTTP run seam (mirroring
``test_run_lifecycle.py``), since that's what "runs end-to-end in demo mode"
and "sandbox targets only" actually mean operationally.
"""
from __future__ import annotations

from sqlmodel import select

from app.core.enums import RiskTier, SafetyClass
from app.models import Check, Suite, SuiteMember, Test
from app.services.risk import effective_risk_tier
from app.services.safety import evaluate_operation_safety
from app.services.seed import CATALOG_CHECK_SPECS

from helpers import target_id as _target_id
from helpers import test_id as _test_id

_JOB_SMOKE_SPEC = dict(CATALOG_CHECK_SPECS)["job_smoke"]


def _job_smoke_check(session) -> Check:
    return session.exec(
        select(Check).where(Check.display_name == _JOB_SMOKE_SPEC["display_name"])
    ).one()


# --------------------------- seed_all() seam ---------------------------
def test_sandbox_functional_suite_is_seeded(seeded_session):
    suite = seeded_session.exec(select(Suite).where(Suite.name == "Sandbox Functional")).one()
    members = seeded_session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()
    assert members, "Sandbox Functional Suite should carry the job-smoke Test"

    test_ids = {m.test_id for m in members}
    tests = seeded_session.exec(select(Test).where(Test.id.in_(test_ids))).all()
    assert any(t.name == "Job create/run/delete smoke" for t in tests)


def test_job_smoke_check_is_confirmed_r2(seeded_session):
    check = _job_smoke_check(seeded_session)
    assert check.confirmed_risk_tier == RiskTier.R2.value
    # The coarse verb guess alone (submit -> POST) would under-classify it —
    # that's exactly why the one-confirm tier (ADR-0017 SS3) is declared.
    coarse = effective_risk_tier(check.operation, None)
    assert coarse.order < RiskTier.R2.order
    effective = effective_risk_tier(check.operation, RiskTier(check.confirmed_risk_tier))
    assert effective == RiskTier.R2


def test_job_smoke_check_only_runs_against_sandbox_targets(seeded_session):
    check = _job_smoke_check(seeded_session)
    confirmed = RiskTier(check.confirmed_risk_tier)

    sandbox = evaluate_operation_safety(check.operation, SafetyClass.SANDBOX, confirmed_tier=confirmed)
    read_only = evaluate_operation_safety(check.operation, SafetyClass.READ_ONLY, confirmed_tier=confirmed)
    prod_for_us = evaluate_operation_safety(check.operation, SafetyClass.PROD_FOR_US, confirmed_tier=confirmed)

    assert sandbox.allowed
    assert not read_only.allowed
    assert not prod_for_us.allowed


def test_job_smoke_check_declares_a_cleanup(seeded_session):
    check = _job_smoke_check(seeded_session)
    cleanup = check.operation.get("params", {}).get("cleanup")
    assert isinstance(cleanup, dict) and cleanup.get("service") and cleanup.get("method"), (
        "the job-smoke Check should declare a cleanup plan via the generic handler's cleanup param")
    assert cleanup["method"] != check.operation.get("method"), (
        "cleanup should be a distinct teardown call, not a repeat of the create/run operation")


def test_job_smoke_check_is_absent_from_ci_gate(seeded_session):
    ci_gate = seeded_session.exec(select(Suite).where(Suite.name == "CI Gate")).one()
    members = seeded_session.exec(select(SuiteMember).where(SuiteMember.suite_id == ci_gate.id)).all()
    test_ids = {m.test_id for m in members}
    tests = seeded_session.exec(select(Test).where(Test.id.in_(test_ids))).all()
    assert all(t.name != "Job create/run/delete smoke" for t in tests)


# --------------------------- demo-mode run + safety gate ---------------------------
def test_job_smoke_runs_end_to_end_in_demo_mode_against_sandbox(client):
    resp = client.post("/api/v1/runs", json={
        "test_ids": [_test_id(client, "Job create/run/delete smoke")],
        "target_ids": [_target_id(client, "sandbox")],
        "depth": "full",
    })
    assert resp.status_code == 202
    resource = client.get(f"/api/v1/runs/{resp.json()['run_id']}").json()
    assert resource["phase"] == "terminal"
    assert resource["verdict"] == "PASS"

    detail = client.get(f"/api/v1/runs/{resource['run_ids'][0]}").json()
    assert "OPERATE" in [s["stage"] for s in detail["stage_outcomes"]]


def test_job_smoke_is_blocked_by_safety_on_non_sandbox_targets(client):
    for safety_class in ("read-only", "prod-for-us"):
        resp = client.post("/api/v1/runs", json={
            "test_ids": [_test_id(client, "Job create/run/delete smoke")],
            "target_ids": [_target_id(client, safety_class)],
            "depth": "full",
        })
        resource = client.get(f"/api/v1/runs/{resp.json()['run_id']}").json()
        assert resource["verdict"] == "BLOCKED_BY_SAFETY", (
            f"expected job-smoke to be blocked against a {safety_class!r} target")
