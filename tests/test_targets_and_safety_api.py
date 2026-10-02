"""HTTP API seam for target registration + safety enforcement (spec #20).

Drives the real contract through ``TestClient`` + SQLite: register a Target with
each safety class, submit runs of script Tests whose SDK calls carry each risk
tier, and assert the verdict / ``BLOCKED_BY_SAFETY`` on ``GET /runs/{id}``;
exercise the test-connection guard, the role-registration guard, and the
override flow. Assertions are on outcomes, not on which of the three layers fired.
"""
from __future__ import annotations

import pytest

from helpers import script_test

R0 = "w.jobs.list()"
R1 = 'w.clusters.create(spark_version="15.4.x-scala2.12", cluster_name="t")'
R2 = 'w.clusters.delete(cluster_id="x")'


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    from app.services import run_service

    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


# --------------------------------- helpers ---------------------------------
def register_target(client, name, safety_class, *, workspace_name=None,
                    identity_handle="kv/sp", roles=None):
    resp = client.post("/api/v1/targets", json={
        "name": name,
        "workspace_name": workspace_name or name,
        "safety_class": safety_class,
        "identity_handle": identity_handle,
        "assumable_roles": roles or [],
    })
    return resp


def make_test(client, call, name, run_as_role=None):
    """An Active script Test whose one check makes ``call``."""
    fields = {"run_as_role": run_as_role} if run_as_role else {}
    return script_test(client, f'with check("{name}"):\n    {call}\n', name, **fields)


def run_verdict(client, test_id, target_id, *, request_source="API",
                override=None, depth="full"):
    # Async contract (ADR-0005): POST returns a run_id; the run is dispatched by
    # the engine (the TestClient completes the background task before returning),
    # then we read the verdict off the run's CheckRun via the verdict resource.
    body = {"test_ids": [test_id], "target_ids": [target_id], "depth": depth,
            "request_source": request_source}
    if override is not None:
        body["override"] = override
    run_id = client.post("/api/v1/runs", json=body).json()["run_id"]
    run_ids = client.get(f"/api/v1/runs/{run_id}").json()["run_ids"]
    assert run_ids, "expected at least one CheckRun"
    return client.get(f"/api/v1/runs/{run_ids[0]}").json()["verdict"]


# ----------------------------- registration ---------------------------------
def test_register_target_with_each_safety_class(client):
    for name, sc in [("ws-ro", "read-only"), ("ws-prod", "prod-for-us"), ("ws-sbx", "sandbox")]:
        resp = register_target(client, name, sc)
        assert resp.status_code == 201, resp.text
        assert resp.json()["safety_class"] == sc


def test_test_connection_failure_blocks_save(client):
    # No identity handle → test-connection fails → save refused (ADR-0003 §3).
    resp = client.post("/api/v1/targets", json={"name": "broken", "safety_class": "read-only"})
    assert resp.status_code == 422
    assert "test-connection failed" in resp.json()["detail"]
    # A malformed handle is refused too.
    resp = client.post("/api/v1/targets", json={
        "name": "broken2", "safety_class": "read-only", "identity_handle": "no-slash"})
    assert resp.status_code == 422


def test_scope_only_identity_handle_blocks_register(client):
    """A secret-scope name without a key (the register-target UI typo) is refused."""
    resp = client.post("/api/v1/targets", json={
        "name": "ws", "safety_class": "read-only",
        "identity_handle": "validation-platform"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "test-connection failed" in detail
    assert "scope/key" in detail
    assert "validation-platform" in detail


def test_test_connection_endpoint(client):
    ok = client.post("/api/v1/targets/test-connection",
                     json={"name": "x", "safety_class": "read-only", "identity_handle": "kv/sp"})
    assert ok.json()["ok"] is True
    bad = client.post("/api/v1/targets/test-connection",
                      json={"name": "x", "safety_class": "read-only", "identity_handle": ""})
    assert bad.json()["ok"] is False


def test_safety_class_is_authoritative_not_name_derived(client):
    # A "dev"-named workspace explicitly registered prod-for-us stays prod-for-us
    # (user story 4) — and is therefore capped at R1, not fooled into sandbox R2.
    resp = register_target(client, "dev-lies", "prod-for-us", workspace_name="dev")
    assert resp.status_code == 201
    target = resp.json()
    assert target["safety_class"] == "prod-for-us"

    t = make_test(client, R2, "delete-on-dev")
    checks = {c["id"]: c for c in client.get("/api/v1/checks").json()}
    assert checks[t["members"][0]["check_id"]]["risk_tier"] == "R2"
    assert run_verdict(client, t["id"], target["id"]) == "BLOCKED_BY_SAFETY"


# --------------------------- role-registration guard -------------------------
def test_registering_role_exceeding_class_is_rejected(client):
    resp = client.post("/api/v1/targets", json={
        "name": "ro-with-admin", "safety_class": "read-only", "identity_handle": "kv/sp",
        "assumable_roles": [{"group_id": "g-admin", "label": "admin", "safety_class": "sandbox"}],
    })
    assert resp.status_code == 422
    assert "exceeds the target's safety class" in resp.json()["detail"]


def test_registering_role_within_class_is_accepted(client):
    resp = register_target(
        client, "ro-with-analyst", "read-only",
        roles=[{"group_id": "g-analyst", "label": "analyst", "safety_class": "read-only"}])
    assert resp.status_code == 201
    assert resp.json()["assumable_roles"][0]["label"] == "analyst"


# ------------------------------- tier gating ---------------------------------
def test_operation_above_class_cap_is_blocked(client):
    ro = register_target(client, "ro-1", "read-only").json()
    r0 = make_test(client, R0, "r0-t")
    r1 = make_test(client, R1, "r1-t")

    assert run_verdict(client, r0["id"], ro["id"]) == "PASS"                # read allowed
    assert run_verdict(client, r1["id"], ro["id"]) == "BLOCKED_BY_SAFETY"   # mutation blocked


def test_prod_for_us_allows_r1_blocks_r2(client):
    prod = register_target(client, "prod-1", "prod-for-us").json()
    r1 = make_test(client, R1, "p-r1")
    r2 = make_test(client, R2, "p-r2")

    assert run_verdict(client, r1["id"], prod["id"]) == "PASS"
    assert run_verdict(client, r2["id"], prod["id"]) == "BLOCKED_BY_SAFETY"


def test_sandbox_allows_r2(client):
    sbx = register_target(client, "sbx-1", "sandbox").json()
    r2 = make_test(client, R2, "s-r2")
    assert run_verdict(client, r2["id"], sbx["id"]) == "PASS"


# --------------------------------- override ----------------------------------
def test_override_authorizes_one_r2_on_prod_for_us_via_gui(client):
    prod = register_target(client, "prod-ov", "prod-for-us").json()
    r2 = make_test(client, R2, "ov-r2")
    override = {"target_name": "prod-ov", "reason": "incident", "confirmed": True}

    verdict = run_verdict(client, r2["id"], prod["id"],
                          request_source="UI_MANUAL", override=override)
    assert verdict == "PASS"


def test_override_is_rejected_for_ci(client):
    prod = register_target(client, "prod-ci", "prod-for-us").json()
    r2 = make_test(client, R2, "ci-r2")
    override = {"target_name": "prod-ci", "reason": "incident", "confirmed": True}

    # Same override payload, but a machine (CI) caller can never escalate.
    verdict = run_verdict(client, r2["id"], prod["id"],
                          request_source="CI", override=override)
    assert verdict == "BLOCKED_BY_SAFETY"


# ------------------------------- run-as-role ---------------------------------
def test_unregistered_run_as_role_is_blocked_before_dispatch(client):
    ro = register_target(client, "ro-role", "read-only").json()
    t = make_test(client, R0, "ghost-role-test", run_as_role="ghost")
    assert run_verdict(client, t["id"], ro["id"]) == "BLOCKED_BY_SAFETY"


def test_registered_run_as_role_is_permitted(client):
    ro = register_target(
        client, "ro-analyst", "read-only",
        roles=[{"group_id": "g-analyst", "label": "analyst", "safety_class": "read-only"}]).json()
    t = make_test(client, R0, "analyst-test", run_as_role="analyst")
    assert run_verdict(client, t["id"], ro["id"]) == "PASS"


# --------------------------- edit + audit trail ------------------------------
def test_safety_class_change_is_audited_as_downgrade(client):
    from sqlmodel import select

    from app.db.session import session_scope
    from app.models import AuditEvent

    prod = register_target(client, "to-downgrade", "prod-for-us").json()
    # prod-for-us (R1 cap) → sandbox (R2 cap) widens the blast radius: a downgrade,
    # which requires the friction ceremony (ADR-0010).
    resp = client.patch(f"/api/v1/targets/{prod['id']}", json={
        "safety_class": "sandbox",
        "ceremony": {"typed_confirmation": "i understand", "reason": "audited downgrade"}})
    assert resp.status_code == 200 and resp.json()["safety_class"] == "sandbox"

    with session_scope() as s:
        events = s.exec(select(AuditEvent).where(
            AuditEvent.action == "target.safety_class.change",
            AuditEvent.entity_id == prod["id"])).all()
    assert len(events) == 1
    assert events[0].before["downgrade"] is True
    assert "DOWNGRADE" in events[0].summary


def test_over_privileged_run_as_role_blocked_before_dispatch(session):
    # Belt-and-braces: even if a role that out-runs its target's class somehow
    # lands in the store (the API guard normally prevents it), the run engine
    # refuses it before dispatch (ADR-0018). Insert such a target directly.
    from app.models import CheckRun, RunRequest, Target
    from app.core.enums import RequestSource, RunStatus, SafetyClass, TestStatus
    from app.services import run_service, script_tests

    target = Target(name="ro-bad", safety_class=SafetyClass.READ_ONLY,
                    assumable_roles=[{"group_id": "g-admin", "label": "admin",
                                      "safety_class": "sandbox"}])
    session.add(target)
    session.commit()
    test, _ = script_tests.create_script_test(
        session, name="read as admin", actor="pytest", run_as_role="admin", status=TestStatus.ACTIVE,
        script=f'with check("read"):\n    {R0}\n')

    req = RunRequest(test_ids=[test.id], target_ids=[target.id], request_source=RequestSource.NIGHTLY)
    session.add(req)
    session.commit()
    run_ids = [r.run_id for r in run_service.expand_request(session, req)]
    assert run_ids

    script_tests.execute_script_group(run_ids)

    session.expire_all()
    assert {session.get(CheckRun, rid).verdict for rid in run_ids} == {RunStatus.BLOCKED_BY_SAFETY}
