"""HTTP API seam for target registration + safety enforcement (spec #20).

Drives the real contract through ``TestClient`` + SQLite: register a Target with
each safety class, submit runs whose operations carry each risk tier, and assert
the verdict / ``BLOCKED_BY_SAFETY`` on ``GET /runs/{id}``; exercise the
test-connection guard, the role-registration guard, and the override flow.
Assertions are on outcomes, not on which of the three layers fired.
"""
from __future__ import annotations


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


def make_check(client, service, method, assertion="the operation succeeds"):
    return client.post("/api/v1/checks", json={
        "display_name": f"{service}.{method}",
        "operation": {"service": service, "method": method},
        "handler_key": "databricks_sdk",
        "assertion": assertion,
    }).json()


def make_test(client, check_id, name, run_as_role=None):
    body = {"name": name, "check_ids": [check_id]}
    if run_as_role:
        body["run_as_role"] = run_as_role
    return client.post("/api/v1/tests", json=body).json()


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

    r2 = make_check(client, "clusters", "delete")
    assert r2["risk_tier"] == "R2"
    t = make_test(client, r2["id"], "delete-on-dev")
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
    r0 = make_test(client, make_check(client, "jobs", "list")["id"], "r0-t")
    r1 = make_test(client, make_check(client, "clusters", "create")["id"], "r1-t")

    assert run_verdict(client, r0["id"], ro["id"]) != "BLOCKED_BY_SAFETY"   # read allowed
    assert run_verdict(client, r1["id"], ro["id"]) == "BLOCKED_BY_SAFETY"   # mutation blocked


def test_prod_for_us_allows_r1_blocks_r2(client):
    prod = register_target(client, "prod-1", "prod-for-us").json()
    r1 = make_test(client, make_check(client, "clusters", "create")["id"], "p-r1")
    r2 = make_test(client, make_check(client, "clusters", "delete")["id"], "p-r2")

    assert run_verdict(client, r1["id"], prod["id"]) != "BLOCKED_BY_SAFETY"
    assert run_verdict(client, r2["id"], prod["id"]) == "BLOCKED_BY_SAFETY"


def test_sandbox_allows_r2(client):
    sbx = register_target(client, "sbx-1", "sandbox").json()
    r2 = make_test(client, make_check(client, "clusters", "delete")["id"], "s-r2")
    assert run_verdict(client, r2["id"], sbx["id"]) != "BLOCKED_BY_SAFETY"


# --------------------------------- override ----------------------------------
def test_override_authorizes_one_r2_on_prod_for_us_via_gui(client):
    prod = register_target(client, "prod-ov", "prod-for-us").json()
    r2 = make_test(client, make_check(client, "clusters", "delete")["id"], "ov-r2")
    override = {"target_name": "prod-ov", "reason": "incident", "confirmed": True}

    verdict = run_verdict(client, r2["id"], prod["id"],
                          request_source="UI_MANUAL", override=override)
    assert verdict != "BLOCKED_BY_SAFETY"


def test_override_is_rejected_for_ci(client):
    prod = register_target(client, "prod-ci", "prod-for-us").json()
    r2 = make_test(client, make_check(client, "clusters", "delete")["id"], "ci-r2")
    override = {"target_name": "prod-ci", "reason": "incident", "confirmed": True}

    # Same override payload, but a machine (CI) caller can never escalate.
    verdict = run_verdict(client, r2["id"], prod["id"],
                          request_source="CI", override=override)
    assert verdict == "BLOCKED_BY_SAFETY"


# ------------------------------- run-as-role ---------------------------------
def test_unregistered_run_as_role_is_blocked_before_dispatch(client):
    ro = register_target(client, "ro-role", "read-only").json()
    r0 = make_check(client, "jobs", "list")
    t = make_test(client, r0["id"], "ghost-role-test", run_as_role="ghost")
    assert run_verdict(client, t["id"], ro["id"]) == "BLOCKED_BY_SAFETY"


def test_registered_run_as_role_is_permitted(client):
    ro = register_target(
        client, "ro-analyst", "read-only",
        roles=[{"group_id": "g-analyst", "label": "analyst", "safety_class": "read-only"}]).json()
    r0 = make_check(client, "jobs", "list")
    t = make_test(client, r0["id"], "analyst-test", run_as_role="analyst")
    assert run_verdict(client, t["id"], ro["id"]) != "BLOCKED_BY_SAFETY"


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
    from app.models import Check, CheckRun, RunRequest, Target
    from app.core.enums import RequestSource, RunStatus, SafetyClass
    from app.services import run_service

    target = Target(name="ro-bad", safety_class=SafetyClass.READ_ONLY,
                    assumable_roles=[{"group_id": "g-admin", "label": "admin",
                                      "safety_class": "sandbox"}])
    check = Check(display_name="read", operation={"service": "jobs", "method": "list", "verb": "GET"},
                  risk_tier="R0")
    session.add(target)
    session.add(check)
    session.commit()
    session.refresh(target)
    session.refresh(check)

    req = RunRequest(request_source=RequestSource.NIGHTLY)
    session.add(req)
    session.commit()
    run = CheckRun(request_id=req.id, check_id=check.id, target_id=target.id,
                   run_as_role="admin", verdict=RunStatus.PENDING)
    session.add(run)
    session.commit()
    session.refresh(run)

    run_service.execute_check_run(run.run_id)

    session.expire_all()
    refreshed = session.get(CheckRun, run.run_id)
    assert refreshed.verdict == RunStatus.BLOCKED_BY_SAFETY
