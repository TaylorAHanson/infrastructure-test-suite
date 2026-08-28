"""HTTP API seam: Check/Test/Suite CRUD + a Run over a seeded Test.

Drives the new model through ``/api/v1/checks``, ``/tests``, ``/suites``,
``/runs`` (spec #19 Testing Decisions). The old ``execution_mode: SMOKE`` /
``step_kind == [PRECHECK, AUTH]`` assertions are gone — a Run now emits stage
outcomes on the canonical spine and connectable/functional is derived.
"""
from __future__ import annotations

from app.core.stages import stage_ids


def test_check_test_suite_roundtrip(client):
    chk = client.post("/api/v1/checks", json={
        "display_name": "roundtrip check",
        "operation": {"service": "catalogs", "method": "list",
                      "params": {"statement": "SHOW CATALOGS", "demo_row_count": 1}},
        "handler_key": "sql_warehouse",
        "assertion": "the query returns at least one row",
    })
    assert chk.status_code == 201
    chk = chk.json()
    assert chk["evaluator"] == "deterministic"
    assert chk["risk_tier"] == "R0"  # a read (list) → coarse R0
    assert chk["compiled_predicate"]["field"] == "row_count"

    tst = client.post("/api/v1/tests", json={"name": "roundtrip test", "check_ids": [chk["id"]]}).json()
    assert tst["members"][0]["check_id"] == chk["id"]

    suite = client.post("/api/v1/suites", json={"name": "roundtrip suite", "test_ids": [tst["id"]]}).json()
    assert suite["test_ids"] == [tst["id"]]

    assert client.get(f"/api/v1/checks/{chk['id']}").json()["id"] == chk["id"]
    assert client.get(f"/api/v1/tests/{tst['id']}").json()["id"] == tst["id"]


def test_run_over_seeded_test_emits_stage_outcomes(client):
    tests = client.get("/api/v1/tests").json()
    sql_test = next(t for t in tests if t["name"] == "SQL Warehouse validation")
    resp = client.post("/api/v1/runs?inline=true", json={"test_ids": [sql_test["id"]], "depth": "full"})
    assert resp.status_code == 202
    run_ids = resp.json()["run_ids"]
    assert run_ids

    detail = client.get(f"/api/v1/runs/{run_ids[0]}").json()
    assert detail["verdict"] == "PASS"
    stages = [s["stage"] for s in detail["stage_outcomes"]]
    assert set(stages).issubset(set(stage_ids()))       # canonical spine only
    assert "OPERATE" in stages                          # full depth reached operate
    assert "PRECHECK" not in stages and "AUTH" not in stages  # no hardcoded StepKind
    assert detail["connectable"] is True                # derived, not stored
    assert detail["functional"] is True


def test_counterfactual_passes_when_operation_is_denied(client):
    tests = client.get("/api/v1/tests").json()
    policy = next(t for t in tests if t["name"] == "Cluster policy enforcement")
    resp = client.post("/api/v1/runs?inline=true", json={"test_ids": [policy["id"]], "depth": "full"})
    run_ids = resp.json()["run_ids"]
    details = [client.get(f"/api/v1/runs/{r}").json() for r in run_ids]

    counterfactuals = [d for d in details if d["expectation"] == "DENY"]
    assert counterfactuals, "the policy test should include a counterfactual"
    for d in counterfactuals:
        assert d["verdict"] == "PASS"          # denied => the counterfactual passes
        assert d["connectable"] is False        # denied at AUTHORIZE


def test_run_verdict_rolls_up_per_test_and_request(client):
    tests = client.get("/api/v1/tests").json()
    sql_test = next(t for t in tests if t["name"] == "SQL Warehouse validation")
    submit = client.post("/api/v1/runs?inline=true", json={"test_ids": [sql_test["id"]], "depth": "full"}).json()
    req = client.get(f"/api/v1/runs/requests/{submit['id']}").json()
    assert req["verdict"] == "PASS"
    assert all(v == "PASS" for v in req["test_rollup"].values())


def test_idempotent_submission(client):
    payload = {"depth": "quick", "idempotency_key": "abc-123"}
    r1 = client.post("/api/v1/runs", json=payload).json()
    r2 = client.post("/api/v1/runs", json=payload).json()
    assert r1["id"] == r2["id"]


def test_blocklisted_operation_refused_by_api(client):
    resp = client.post("/api/v1/checks", json={
        "display_name": "danger",
        "operation": {"service": "workspaces", "method": "delete"},
        "assertion": "the operation succeeds",
    })
    assert resp.status_code == 422
    assert "blocklisted" in resp.json()["detail"].lower()


def test_yaml_export_of_a_test(client):
    tests = client.get("/api/v1/tests").json()
    y = client.get(f"/api/v1/tests/{tests[0]['id']}/yaml").json()["yaml"]
    assert "test:" in y and "checks:" in y


def test_check_versioning_and_rollback(client):
    chk = client.post("/api/v1/checks", json={
        "display_name": "v-one",
        "operation": {"service": "jobs", "method": "list"},
        "assertion": "the operation succeeds",
    }).json()
    assert chk["version"] == 1

    updated = client.patch(f"/api/v1/checks/{chk['id']}", json={"display_name": "v-two"}).json()
    assert updated["version"] == 2 and updated["display_name"] == "v-two"

    versions = client.get(f"/api/v1/checks/{chk['id']}/versions").json()
    assert len(versions) >= 2

    rolled = client.post(f"/api/v1/checks/{chk['id']}/rollback/1").json()
    assert rolled["display_name"] == "v-one" and rolled["version"] == 3
