"""The seeded catalog: connectivity, health and posture Tests are scripts that assert what they claim.

The seed used to be ~30 list/get calls that proved only that the control plane
answered. These pin the replacement's guarantees: every seeded script is valid
and every check in it can fail on its own terms; the quick tier is read-only and
gates CI; the compute tier and the posture battery never do; the connectivity
Tests are read-only and make up the Connectivity Suite; and the whole catalog
passes in demo mode against a healthy stand-in workspace.
"""
from __future__ import annotations

import pytest
from sqlmodel import select

from app.core.enums import RiskTier
from app.models import Suite, SuiteMember, Test
from app.script_runtime.analysis import analyze
from app.services.seed.registry import discover_contributions
from helpers import suite_id, target_id

QUICK = {"Workspace access", "Unity Catalog data path"}
COMPUTE = {"Classic compute", "Serverless compute", "SQL warehouse cold start",
           "Unity Catalog write round-trip"}
POSTURE = {"Exfiltration controls", "Token hygiene", "Network access controls", "User isolation",
           "Admin footprint"}
CONNECTIVITY = ["Egress connectivity", "SQL Warehouse validation", "Unity Catalog access"]
COMPUTE_SUITE = "Post-apply health: compute & network"
POSTURE_SUITE = "Security posture (drift)"
CONNECTIVITY_SUITE = "Connectivity Suite"


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    import app.services.run_service as rs

    monkeypatch.setattr(rs, "_demo_mode", lambda: True)


def _script_specs():
    return {spec.name: spec for c in discover_contributions() for spec in c.tests.values() if spec.script}


def _suite_tests(session, name: str) -> set[str]:
    suite = session.exec(select(Suite).where(Suite.name == name)).one()
    members = session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()
    return {session.get(Test, m.test_id).name for m in members}


def test_connectivity_health_and_posture_tests_are_seeded_as_scripts(seeded_session):
    tests = {t.name: t for t in seeded_session.exec(select(Test)).all()}
    for name in QUICK | COMPUTE | POSTURE | set(CONNECTIVITY):
        assert tests[name].script, f"{name!r} should be a script test"


@pytest.mark.parametrize("name", sorted(QUICK | COMPUTE | POSTURE | set(CONNECTIVITY)))
def test_every_seeded_check_can_fail_on_its_own_terms(name):
    out = analyze(_script_specs()[name].script)
    assert out["ok"], out["errors"]
    assert out["checks"], f"{name!r} declares no checks"
    assert not [w for w in out["warnings"] if "has no assert" in w["message"]], out["warnings"]


def test_the_quick_tier_is_read_only_and_is_exactly_the_ci_gate(seeded_session):
    specs = _script_specs()
    for name in QUICK:
        out = analyze(specs[name].script)
        assert out["max_risk_tier"] == RiskTier.R0.value, name
        assert not any(c["advisory"] for c in out["checks"]), name
    assert _suite_tests(seeded_session, "CI Gate") == QUICK


def test_the_compute_tier_mutates_and_never_gates_ci(seeded_session):
    specs = _script_specs()
    for name in COMPUTE:
        assert analyze(specs[name].script)["max_risk_tier"] == RiskTier.R1.value, name
    assert _suite_tests(seeded_session, COMPUTE_SUITE) == COMPUTE
    assert not (_suite_tests(seeded_session, "CI Gate") & COMPUTE)


def test_posture_is_its_own_suite_outside_the_gate(seeded_session):
    assert _suite_tests(seeded_session, POSTURE_SUITE) == POSTURE
    assert not (_suite_tests(seeded_session, "CI Gate") & POSTURE)


def test_the_connectivity_suite_is_the_three_read_only_connectivity_tests(seeded_session):
    specs = _script_specs()
    for name in CONNECTIVITY:
        out = analyze(specs[name].script)
        assert out["max_risk_tier"] == RiskTier.R0.value, name
        assert len(out["checks"]) == 1 and not out["checks"][0]["advisory"], name
    suite = seeded_session.exec(select(Suite).where(Suite.name == CONNECTIVITY_SUITE)).one()
    assert suite.description == "Reachability across producer/consumer paths."
    members = seeded_session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)
                                  .order_by(SuiteMember.sort_order)).all()
    assert [seeded_session.get(Test, m.test_id).name for m in members] == CONNECTIVITY
    assert not (_suite_tests(seeded_session, "CI Gate") & set(CONNECTIVITY))


def test_the_counterfactual_targets_a_real_ungranted_object():
    out = analyze(_script_specs()["Unity Catalog data path"].script)
    (deny,) = [c for c in out["checks"] if c["kind"] == "counterfactual"]
    assert deny["expected_stage"] == "AUTHORIZE"


def _run_suite(client, name, safety_class, depth):
    run_id = client.post("/api/v1/runs", json={"suite_ids": [suite_id(client, name)],
                                               "target_ids": [target_id(client, safety_class)],
                                               "depth": depth}).json()["run_id"]
    return client.get(f"/api/v1/runs/{run_id}").json()


def test_the_ci_gate_passes_in_demo_mode_on_a_read_only_target(client):
    resource = _run_suite(client, "CI Gate", "read-only", "quick")
    assert resource["verdict"] == "PASS", resource


def test_the_compute_tier_passes_at_full_depth_on_a_sandbox(client):
    resource = _run_suite(client, COMPUTE_SUITE, "sandbox", "full")
    assert resource["verdict"] == "PASS", resource
    details = [client.get(f"/api/v1/runs/{r}").json() for r in resource["run_ids"]]
    verdicts = {d["verdict"] for d in details}
    # The cold-start test has no classic warehouse configured on the seeded target.
    assert verdicts <= {"PASS", "SKIPPED"}, verdicts
    assert "PASS" in verdicts


def test_the_compute_tier_is_blocked_on_a_read_only_target(client):
    resource = _run_suite(client, COMPUTE_SUITE, "read-only", "full")
    assert resource["verdict"] == "BLOCKED_BY_SAFETY", resource


def test_the_compute_tier_is_skipped_at_quick_depth(client):
    resource = _run_suite(client, COMPUTE_SUITE, "sandbox", "quick")
    details = [client.get(f"/api/v1/runs/{r}").json() for r in resource["run_ids"]]
    assert {d["verdict"] for d in details} == {"SKIPPED"}


def test_the_posture_suite_passes_in_demo_mode(client):
    resource = _run_suite(client, POSTURE_SUITE, "read-only", "quick")
    assert resource["verdict"] == "PASS", resource


def test_the_connectivity_suite_passes_in_demo_mode_and_records_its_evidence(client):
    from app.db.session import session_scope
    from app.models import CheckRun

    resource = _run_suite(client, CONNECTIVITY_SUITE, "read-only", "quick")
    assert resource["verdict"] == "PASS", resource
    with session_scope() as s:
        runs = s.exec(select(CheckRun).where(CheckRun.run_id.in_(resource["run_ids"]))).all()
        evidence = {s.get(Test, r.test_id).name: r.evidence for r in runs}
    assert {r.verdict for r in runs} == {"PASS"} and set(evidence) == set(CONNECTIVITY)
    assert evidence["SQL Warehouse validation"]["rows"] == [{"healthcheck": "1"}]
    assert "main" in evidence["Unity Catalog access"]["catalogs"]
    assert evidence["Egress connectivity"]["status"] == 200
    assert evidence["Egress connectivity"]["url"] == "https://example.com"
