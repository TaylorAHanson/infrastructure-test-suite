"""The seeded catalog: health and posture Tests are scripts that assert what they claim.

The seed used to be ~30 list/get calls that proved only that the control plane
answered. These pin the replacement's guarantees: every seeded script is valid
and every check in it can fail on its own terms; the quick tier is read-only and
gates CI; the compute tier and the posture battery never do; and the whole
catalog passes in demo mode against a healthy stand-in workspace.
"""
from __future__ import annotations

import pytest
from sqlmodel import select

from app.core.enums import RiskTier
from app.models import Suite, SuiteMember, Test
from app.script_runtime.analysis import analyze
from app.services.seed import TUTORIAL_TAGS
from app.services.seed.registry import discover_contributions
from helpers import suite_id, target_id

QUICK = {"Workspace access", "Unity Catalog data path"}
COMPUTE = {"Classic compute", "Serverless compute", "SQL warehouse cold start",
           "Unity Catalog write round-trip"}
POSTURE = {"Exfiltration controls", "Token hygiene", "Network access controls", "User isolation",
           "Admin footprint"}
COMPUTE_SUITE = "Post-apply health: compute & network"
POSTURE_SUITE = "Security posture (drift)"


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


def test_health_and_posture_tests_are_seeded_as_scripts(seeded_session):
    tests = {t.name: t for t in seeded_session.exec(select(Test)).all()}
    for name in QUICK | COMPUTE | POSTURE:
        assert tests[name].script, f"{name!r} should be a script test"
        assert not (set(TUTORIAL_TAGS) & set(tests[name].tags or [])), name


@pytest.mark.parametrize("name", sorted(QUICK | COMPUTE | POSTURE))
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
    gate = seeded_session.exec(select(Suite).where(Suite.name == "CI Gate")).one()
    assert gate.deterministic_only is True


def test_the_compute_tier_mutates_and_never_gates_ci(seeded_session):
    specs = _script_specs()
    for name in COMPUTE:
        assert analyze(specs[name].script)["max_risk_tier"] == RiskTier.R1.value, name
    assert _suite_tests(seeded_session, COMPUTE_SUITE) == COMPUTE
    assert not (_suite_tests(seeded_session, "CI Gate") & COMPUTE)


def test_posture_is_its_own_suite_outside_the_gate(seeded_session):
    assert _suite_tests(seeded_session, POSTURE_SUITE) == POSTURE
    assert not (_suite_tests(seeded_session, "CI Gate") & POSTURE)


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
