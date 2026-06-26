"""End-to-end run lifecycle + idempotency + remote-agent signed results."""
from __future__ import annotations


def test_submit_inline_run_and_fetch_detail(client):
    tests = client.get("/api/v1/tests").json()
    sqlw = next(t for t in tests if t["executor_key"] == "sql_warehouse_smoke")
    resp = client.post(
        "/api/v1/runs?inline=true",
        json={"test_definition_ids": [sqlw["id"]], "execution_mode": "SMOKE"},
    )
    assert resp.status_code == 202
    run_ids = resp.json()["run_ids"]
    assert run_ids
    detail = client.get(f"/api/v1/runs/{run_ids[0]}").json()
    assert detail["status"] == "PASSED"
    assert detail["connectivity_outcome"] == "CONNECTABLE"
    assert [s["step_kind"] for s in detail["steps"]][:2] == ["PRECHECK", "AUTH"]


def test_idempotent_submission(client):
    payload = {"execution_mode": "PREFLIGHT", "idempotency_key": "abc-123"}
    r1 = client.post("/api/v1/runs", json=payload).json()
    r2 = client.post("/api/v1/runs", json=payload).json()
    assert r1["id"] == r2["id"]


def test_remote_agent_signed_results(client):
    import hashlib
    import hmac
    import json

    # A remote-agent test (Power BI) yields a PARTIAL awaiting-agent run.
    tests = client.get("/api/v1/tests").json()
    pbi = next(t for t in tests if "powerbi" in t["tags"])
    resp = client.post("/api/v1/runs?inline=true",
                       json={"test_definition_ids": [pbi["id"]], "execution_mode": "PREFLIGHT"})
    run_id = resp.json()["run_ids"][0]

    # Without a configured secret, submission is accepted unsigned (dev mode).
    steps = [{"step_kind": "FUNCTIONAL", "outcome": "PASS", "connectivity": "CONNECTABLE",
              "message": "Power BI reached warehouse from BI network.", "evidence": {}}]
    body = {"run_id": run_id, "agent_id": "agent_pbi", "signature": "", "steps": steps}
    out = client.post("/api/v1/agents/results", json=body)
    assert out.status_code == 200
    detail = client.get(f"/api/v1/runs/{run_id}").json()
    assert detail["probe_outcome"] == "PASS"


def test_analytics_endpoints(client):
    # Run a couple of probes so analytics have data.
    tests = client.get("/api/v1/tests").json()
    sqlw = next(t for t in tests if t["executor_key"] == "sql_warehouse_smoke")
    client.post("/api/v1/runs?inline=true",
                json={"test_definition_ids": [sqlw["id"]], "execution_mode": "SMOKE"})
    assert client.get("/api/v1/analytics/overview").json()["total_runs"] >= 1
    assert isinstance(client.get("/api/v1/analytics/failure-rate-over-time").json(), list)
    assert "dns_failures" in client.get("/api/v1/analytics/privatelink-failures").json()
