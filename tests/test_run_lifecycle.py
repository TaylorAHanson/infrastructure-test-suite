"""HTTP API seam: the shared Run API + CRUD (spec #21, ADR-0005/0007/0011).

Drives the **async run contract** through ``TestClient`` + SQLite: ``POST /runs``
returns a ``run_id``; the caller polls ``GET /runs/{run_id}`` until the phase is
terminal (the TestClient completes the dispatch background task before returning,
so a single ``GET`` after ``POST`` observes the terminal state). Assertions are on
observable phases/verdicts — never internal scheduler state.

There is no ``idempotency_key``, no ``inline=true`` shortcut, and no
``execution_mode``: a retried ``POST`` is just another run, and depth is the
single ``quick``/``full`` dial.
"""
from __future__ import annotations

from app.core.stages import stage_ids

from helpers import target_id as _target_id
from helpers import test_id as _test_id


# --------------------------- helpers ---------------------------
def poll(client, run_id):
    """Fetch the verdict resource for a run (already terminal under TestClient)."""
    return client.get(f"/api/v1/runs/{run_id}").json()


def submit(client, **body):
    """POST a run and return (run_id, verdict-resource)."""
    run_id = client.post("/api/v1/runs", json=body).json()["run_id"]
    return run_id, poll(client, run_id)


# --------------------------- CRUD roundtrip ---------------------------
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


# --------------------------- async run contract ---------------------------
def test_post_runs_returns_run_id_and_get_reaches_terminal(client):
    resp = client.post("/api/v1/runs", json={"test_ids": [_test_id(client, "SQL Warehouse validation")],
                                             "target_ids": [_target_id(client, "read-only")], "depth": "full"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"] == body["id"]           # the id the caller polls
    resource = poll(client, body["run_id"])
    assert resource["phase"] == "terminal"
    assert resource["verdict"] == "PASS"
    assert resource["run_ids"]                    # fan-out CheckRuns present on the resource


def test_run_over_seeded_test_emits_stage_outcomes(client):
    run_id, resource = submit(client, test_ids=[_test_id(client, "SQL Warehouse validation")],
                              target_ids=[_target_id(client, "read-only")], depth="full")
    assert resource["verdict"] == "PASS"

    detail = client.get(f"/api/v1/runs/{resource['run_ids'][0]}").json()
    stages = [s["stage"] for s in detail["stage_outcomes"]]
    assert set(stages).issubset(set(stage_ids()))       # canonical spine only
    assert "OPERATE" in stages                          # full depth reached operate
    assert "PRECHECK" not in stages and "AUTH" not in stages  # no hardcoded StepKind
    assert detail["connectable"] is True                # derived, not stored
    assert detail["functional"] is True


def test_depth_defaults_to_quick_and_full_is_opt_in(client):
    # Unspecified depth is the cheap/fast one (ADR-0007 §3): quick runs only the
    # connectivity end of the spine, so OPERATE never runs.
    _, quick = submit(client, test_ids=[_test_id(client, "SQL Warehouse validation")],
                      target_ids=[_target_id(client, "read-only")])
    qdetail = client.get(f"/api/v1/runs/{quick['run_ids'][0]}").json()
    qstages = [s["stage"] for s in qdetail["stage_outcomes"]]
    assert "OPERATE" not in qstages and "VERIFY" not in qstages
    assert qdetail["connectable"] is True
    assert qdetail["functional"] is None                # no functional stage ran

    # full is the explicit opt-in and reaches OPERATE.
    _, full = submit(client, test_ids=[_test_id(client, "SQL Warehouse validation")],
                     target_ids=[_target_id(client, "read-only")], depth="full")
    fstages = [s["stage"] for s in client.get(f"/api/v1/runs/{full['run_ids'][0]}").json()["stage_outcomes"]]
    assert "OPERATE" in fstages


def test_counterfactual_passes_when_operation_is_denied(client):
    # Cluster-policy Checks are mutating (R1); run against sandbox so the safety
    # gate permits the attempt and the *policy* denial fails it closed.
    run_id, resource = submit(client, test_ids=[_test_id(client, "Cluster policy enforcement")],
                              target_ids=[_target_id(client, "sandbox")], depth="full")
    details = [client.get(f"/api/v1/runs/{r}").json() for r in resource["run_ids"]]
    counterfactuals = [d for d in details if d["expectation"] == "DENY"]
    assert counterfactuals, "the policy test should include a counterfactual"
    for d in counterfactuals:
        assert d["verdict"] == "PASS"          # denied => the counterfactual passes
        assert d["connectable"] is False        # denied at AUTHORIZE


def test_verdict_rolls_up_per_test_on_the_resource(client):
    _, resource = submit(client, test_ids=[_test_id(client, "SQL Warehouse validation")],
                         target_ids=[_target_id(client, "read-only")], depth="full")
    assert resource["verdict"] == "PASS"
    assert all(v == "PASS" for v in resource["test_rollup"].values())
    assert resource["failure_summary"] == {"gating": {}, "advisory": {}}


# --------------------------- fail-closed mapping ---------------------------
def test_fail_closed_mapping_only_pass_is_passing(client):
    # A mutation above the read-only cap → BLOCKED_BY_SAFETY, which is non-pass
    # and must block the pipeline (ADR-0005 §5). Assert it is not PASS and the
    # phase is terminal so the caller stops polling and gates.
    chk = client.post("/api/v1/checks", json={
        "display_name": "over-cap delete", "operation": {"service": "clusters", "method": "delete"},
        "assertion": "the operation succeeds"}).json()
    tst = client.post("/api/v1/tests", json={"name": "over-cap", "check_ids": [chk["id"]]}).json()
    _, resource = submit(client, test_ids=[tst["id"]], target_ids=[_target_id(client, "read-only")], depth="full")
    assert resource["phase"] == "terminal"
    assert resource["verdict"] == "BLOCKED_BY_SAFETY"
    assert resource["verdict"] != "PASS"        # fail-closed: only PASS passes


def test_unknown_target_is_a_hard_error_not_a_silent_pass(client):
    # ADR-0005 §3: a named-but-unregistered target must fail loudly, never drop
    # silently (which could run against nothing and still roll up to PASS).
    resp = client.post("/api/v1/runs", json={
        "test_ids": [_test_id(client, "SQL Warehouse validation")],
        "target_ids": ["tgt_does_not_exist"], "depth": "full"})
    assert resp.status_code == 400
    assert "unknown" in resp.json()["detail"].lower()


# --------------------------- blocklist + CRUD/versioning ---------------------------
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
