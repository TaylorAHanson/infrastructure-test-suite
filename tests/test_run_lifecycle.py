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

import pytest

from app.core.stages import stage_ids

from helpers import script_test
from helpers import target_id as _target_id

READS = ('with check("lists warehouses"):\n    record("n", len(w.warehouses.list()))\n'
         'with check("knows its identity"):\n    w.current_user.me()\n')
CREATES = 'with check("creates a cluster"):\n    w.clusters.create(spark_version="15.4.x-scala2.12", cluster_name="t")\n'


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    from app.services import run_service

    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


# --------------------------- helpers ---------------------------
def poll(client, run_id):
    """Fetch the verdict resource for a run (already terminal under TestClient)."""
    return client.get(f"/api/v1/runs/{run_id}").json()


def submit(client, **body):
    """POST a run and return (run_id, verdict-resource)."""
    run_id = client.post("/api/v1/runs", json=body).json()["run_id"]
    return run_id, poll(client, run_id)


# --------------------------- CRUD roundtrip ---------------------------
def test_test_check_suite_roundtrip(client):
    tst = script_test(client, READS, "roundtrip test", active=False)
    assert tst["status"] == "draft"
    cid = tst["members"][0]["check_id"]
    chk = client.get(f"/api/v1/checks/{cid}").json()
    assert chk["handler_key"] == "script" and chk["display_name"] == "lists warehouses"
    assert chk["evaluator"] == "deterministic"
    assert chk["risk_tier"] == "R0"  # a read (list) → coarse R0
    assert cid in {c["id"] for c in client.get("/api/v1/checks").json()}

    suite = client.post("/api/v1/suites", json={"name": "roundtrip suite", "test_ids": [tst["id"]]}).json()
    assert suite["test_ids"] == [tst["id"]]
    assert client.get(f"/api/v1/tests/{tst['id']}").json()["id"] == tst["id"]


def test_the_structured_authoring_routes_are_gone(client):
    tst = script_test(client, READS, "routes", active=False)
    cid = tst["members"][0]["check_id"]
    suite = client.post("/api/v1/suites", json={"name": "s", "test_ids": [tst["id"]]}).json()
    gone = [
        client.post("/api/v1/checks", json={"display_name": "x"}),
        client.post("/api/v1/checks/compile", json={"assertion": "the operation succeeds"}),
        client.patch(f"/api/v1/checks/{cid}", json={"display_name": "x"}),
        client.get(f"/api/v1/checks/{cid}/versions"),
        client.post(f"/api/v1/checks/{cid}/rollback/1"),
        client.post("/api/v1/tests", json={"name": "x", "check_ids": [cid]}),
        client.get(f"/api/v1/tests/{tst['id']}/yaml"),
        client.get(f"/api/v1/suites/{suite['id']}/yaml"),
    ]
    assert all(r.status_code in (404, 405) for r in gone), [(r.request.url.path, r.status_code) for r in gone]
    assert client.get(f"/api/v1/checks/{cid}").json()["version"] == 1


# --------------------------- async run contract ---------------------------
def test_post_runs_returns_run_id_and_get_reaches_terminal(client):
    test = script_test(client, READS, "async")
    resp = client.post("/api/v1/runs", json={"test_ids": [test["id"]],
                                             "target_ids": [_target_id(client, "read-only")], "depth": "full"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"] == body["id"]           # the id the caller polls
    resource = poll(client, body["run_id"])
    assert resource["phase"] == "terminal"
    assert resource["verdict"] == "PASS"
    assert len(resource["run_ids"]) == 2          # one fan-out CheckRun per check


def test_a_script_run_emits_stage_outcomes_on_the_canonical_spine(client):
    test = script_test(client, READS, "stages")
    run_id, resource = submit(client, test_ids=[test["id"]],
                              target_ids=[_target_id(client, "read-only")], depth="full")
    assert resource["verdict"] == "PASS"

    detail = client.get(f"/api/v1/runs/{resource['run_ids'][0]}").json()
    stages = [s["stage"] for s in detail["stage_outcomes"]]
    assert set(stages).issubset(set(stage_ids()))       # canonical spine only
    assert "OPERATE" in stages
    assert "PRECHECK" not in stages and "AUTH" not in stages  # no hardcoded StepKind
    assert detail["connectable"] is True                # derived, not stored
    assert detail["functional"] is True


def test_a_suite_selection_runs_its_tests(client):
    a, b = script_test(client, READS, "suite a"), script_test(client, READS, "suite b")
    suite = client.post("/api/v1/suites", json={"name": "picked", "test_ids": [a["id"], b["id"]]}).json()
    _, resource = submit(client, suite_ids=[suite["id"]], target_ids=[_target_id(client, "read-only")])
    assert resource["verdict"] == "PASS", resource
    assert set(resource["test_rollup"]) == {a["id"], b["id"]}


def test_depth_defaults_to_quick_and_full_is_opt_in(client):
    # Unspecified depth is the cheap/fast one (ADR-0007 §3): a quick run makes
    # only read calls, so a mutating check is skipped, never attempted.
    test = script_test(client, CREATES, "depth")
    sandbox = _target_id(client, "sandbox")
    _, quick = submit(client, test_ids=[test["id"]], target_ids=[sandbox])
    assert quick["depth"] == "quick"
    qdetail = client.get(f"/api/v1/runs/{quick['run_ids'][0]}").json()
    assert qdetail["verdict"] == "SKIPPED"
    assert [s["outcome"] for s in qdetail["stage_outcomes"] if s["stage"] == "OPERATE"] == ["SKIP"]

    # full is the explicit opt-in and makes the call.
    _, full = submit(client, test_ids=[test["id"]], target_ids=[sandbox], depth="full")
    fdetail = client.get(f"/api/v1/runs/{full['run_ids'][0]}").json()
    assert fdetail["verdict"] == "PASS"
    assert "OPERATE" in [s["stage"] for s in fdetail["stage_outcomes"]]


def test_counterfactual_passes_when_operation_is_denied(client):
    test = script_test(client, 'with expect_deny("the forbidden table is denied"):\n'
                               '    sql("SELECT * FROM main.health.forbidden")\n', "denied")
    _, resource = submit(client, test_ids=[test["id"]],
                         target_ids=[_target_id(client, "read-only")], depth="full")
    (detail,) = [client.get(f"/api/v1/runs/{r}").json() for r in resource["run_ids"]]
    assert detail["expectation"] == "DENY"
    assert detail["verdict"] == "PASS"          # denied => the counterfactual passes
    assert detail["connectable"] is False       # denied at AUTHORIZE


def test_verdict_rolls_up_per_test_on_the_resource(client):
    test = script_test(client, READS, "rollup")
    _, resource = submit(client, test_ids=[test["id"]],
                         target_ids=[_target_id(client, "read-only")], depth="full")
    assert resource["verdict"] == "PASS"
    assert resource["test_rollup"] == {test["id"]: "PASS"}
    assert resource["failure_summary"] == {"gating": {}, "advisory": {}}


# --------------------------- fail-closed mapping ---------------------------
def test_fail_closed_mapping_only_pass_is_passing(client):
    # A mutation above the read-only cap → BLOCKED_BY_SAFETY, which is non-pass
    # and must block the pipeline (ADR-0005 §5). Assert it is not PASS and the
    # phase is terminal so the caller stops polling and gates.
    tst = script_test(client, 'with check("over-cap delete"):\n    w.clusters.delete(cluster_id="x")\n', "over-cap")
    _, resource = submit(client, test_ids=[tst["id"]], target_ids=[_target_id(client, "read-only")], depth="full")
    assert resource["phase"] == "terminal"
    assert resource["verdict"] == "BLOCKED_BY_SAFETY"
    assert resource["verdict"] != "PASS"        # fail-closed: only PASS passes


def test_unknown_target_is_a_hard_error_not_a_silent_pass(client):
    # ADR-0005 §3: a named-but-unregistered target must fail loudly, never drop
    # silently (which could run against nothing and still roll up to PASS).
    test = script_test(client, READS, "nowhere")
    resp = client.post("/api/v1/runs", json={
        "test_ids": [test["id"]], "target_ids": ["tgt_does_not_exist"], "depth": "full"})
    assert resp.status_code == 400
    assert "unknown" in resp.json()["detail"].lower()


def test_a_check_selection_is_refused_not_widened_to_everything(client):
    # check_ids is no longer a selection; ignoring it would leave the selection
    # empty, which means "run every Test".
    before = len(client.get("/api/v1/runs/requests").json())
    resp = client.post("/api/v1/runs", json={"check_ids": ["chk_x"], "target_ids": [_target_id(client, "read-only")]})
    assert resp.status_code == 422
    assert "check_ids" in resp.text
    assert len(client.get("/api/v1/runs/requests").json()) == before


# --------------------------- blocklist ---------------------------
def test_blocklisted_call_refused_by_api(client):
    resp = client.post("/api/v1/tests/script", json={
        "name": "danger", "script": 'with check("danger"):\n    w.metastores.delete(id="m")\n'})
    assert resp.status_code == 422
    assert any("blocklist" in e["message"] for e in resp.json()["detail"]["analysis"]["errors"])
