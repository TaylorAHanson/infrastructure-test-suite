"""HTTP API seam for the host-identity access model + friction ceremony (#37).

The platform has **no app-local authorization** (ADR-0010): authentication is
the host Databricks App identity and every authenticated human has full
capability. The two safety-gate-defeating edits — a **safety-class downgrade**
and a per-run **safety override** — are guarded by a **friction ceremony**
(typed confirmation + reason), *not* a permission gate; **R3 stays globally
forbidden** regardless, and the override **rejects machine identities**. Both
gated edits are **audited with their ceremony reason**.

These drive the real contract through ``TestClient`` + SQLite and assert
observable outcomes, not internal wiring.
"""
from __future__ import annotations

import pytest

from helpers import script_test

R2 = 'w.clusters.delete(cluster_id="x")'


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    from app.services import run_service

    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


# --------------------------------- helpers ---------------------------------
def register(client, name, safety_class, *, headers=None, identity_handle="kv/sp"):
    return client.post("/api/v1/targets", headers=headers or {}, json={
        "name": name, "workspace_name": name, "safety_class": safety_class,
        "identity_handle": identity_handle, "assumable_roles": [],
    })


def make_test(client, call, name):
    """An Active script Test whose one check makes ``call``."""
    return script_test(client, f'with check("{name}"):\n    {call}\n', name)


def run_verdict(client, test_id, target_id, *, request_source="API", override=None, depth="full"):
    body = {"test_ids": [test_id], "target_ids": [target_id], "depth": depth,
            "request_source": request_source}
    if override is not None:
        body["override"] = override
    run_id = client.post("/api/v1/runs", json=body).json()["run_id"]
    run_ids = client.get(f"/api/v1/runs/{run_id}").json()["run_ids"]
    assert run_ids, "expected at least one CheckRun"
    return client.get(f"/api/v1/runs/{run_ids[0]}").json()["verdict"]


CEREMONY = {"typed_confirmation": "i understand", "reason": "quarterly drill"}


# ---------------- 1. full capability, no role gate ----------------
def test_any_authenticated_human_has_full_capability(client):
    # No role/ACL is configured anywhere: the same plain caller can register a
    # target, author a Test, trigger a run, downgrade the class (ceremony), and
    # exercise the override — every capability, no 403 at any step.
    prod = register(client, "cap-prod", "prod-for-us").json()
    test = make_test(client, R2, "cap-del")

    # Trigger (capability) — a within-cap read is fine; the R2 here is blocked by
    # tier, never by a *permission* gate (that's the point of the model).
    assert run_verdict(client, test["id"], prod["id"]) == "BLOCKED_BY_SAFETY"

    # Downgrade prod-for-us → sandbox with the ceremony — allowed for any human.
    resp = client.patch(f"/api/v1/targets/{prod['id']}",
                        json={"safety_class": "sandbox", "ceremony": CEREMONY})
    assert resp.status_code == 200, resp.text
    assert resp.json()["safety_class"] == "sandbox"


# ---------------- 2. downgrade ceremony ----------------
def test_downgrade_without_ceremony_is_rejected(client):
    prod = register(client, "dg-noc", "prod-for-us").json()
    # prod-for-us (R1) → sandbox (R2) widens blast radius: a downgrade needs the
    # ceremony. A bare PATCH is refused.
    resp = client.patch(f"/api/v1/targets/{prod['id']}", json={"safety_class": "sandbox"})
    assert resp.status_code == 422
    assert resp.json()["safety_class"] != "sandbox" if "safety_class" in resp.json() else True
    # The class did not change.
    assert client.get(f"/api/v1/targets/{prod['id']}").json()["safety_class"] == "prod-for-us"


def test_downgrade_with_wrong_typed_confirmation_is_rejected(client):
    prod = register(client, "dg-wrong", "prod-for-us").json()
    resp = client.patch(f"/api/v1/targets/{prod['id']}", json={
        "safety_class": "sandbox",
        "ceremony": {"typed_confirmation": "yes do it", "reason": "x"}})
    assert resp.status_code == 422
    assert client.get(f"/api/v1/targets/{prod['id']}").json()["safety_class"] == "prod-for-us"


def test_downgrade_without_reason_is_rejected(client):
    prod = register(client, "dg-noreason", "prod-for-us").json()
    resp = client.patch(f"/api/v1/targets/{prod['id']}", json={
        "safety_class": "sandbox",
        "ceremony": {"typed_confirmation": "i understand", "reason": "   "}})
    assert resp.status_code == 422


def test_downgrade_with_ceremony_succeeds_and_is_audited_with_reason(client):
    prod = register(client, "dg-ok", "prod-for-us").json()
    resp = client.patch(f"/api/v1/targets/{prod['id']}", json={
        "safety_class": "sandbox",
        "ceremony": {"typed_confirmation": "  I Understand  ", "reason": "incident-99 remediation"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["safety_class"] == "sandbox"

    events = client.get("/api/v1/audit",
                        params={"event_type": "target.safety_class.change"}).json()
    assert len(events) == 1
    assert events[0]["before"]["downgrade"] is True
    assert events[0]["after"]["ceremony_reason"] == "incident-99 remediation"
    assert "DOWNGRADE" in events[0]["summary"]


def test_upgrade_needs_no_ceremony(client):
    # Narrowing the blast radius (sandbox → prod-for-us) is not a downgrade and
    # needs no ceremony — friction guards only the gate-defeating direction.
    sbx = register(client, "up-1", "sandbox").json()
    resp = client.patch(f"/api/v1/targets/{sbx['id']}", json={"safety_class": "prod-for-us"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["safety_class"] == "prod-for-us"


# ---------------- 3. override ceremony + machine rejection ----------------
def test_override_requires_matching_typed_confirmation(client):
    prod = register(client, "ov-conf", "prod-for-us").json()
    test = make_test(client, R2, "ov-c")
    # A blank / mismatched typed target name is not a valid confirmation.
    blank = {"target_name": "", "reason": "r", "confirmed": True}
    assert run_verdict(client, test["id"], prod["id"],
                       request_source="UI_MANUAL", override=blank) == "BLOCKED_BY_SAFETY"

    good = {"target_name": "ov-conf", "reason": "r", "confirmed": True}
    assert run_verdict(client, test["id"], prod["id"],
                       request_source="UI_MANUAL", override=good) == "PASS"


def test_override_is_rejected_for_a_machine_identity(client):
    prod = register(client, "ov-machine", "prod-for-us").json()
    test = make_test(client, R2, "ov-m")
    override = {"target_name": "ov-machine", "reason": "r", "confirmed": True}
    # Same valid ceremony payload, but a machine (CI) caller can never escalate.
    assert run_verdict(client, test["id"], prod["id"],
                       request_source="CI", override=override) == "BLOCKED_BY_SAFETY"


def test_a_machine_caller_cannot_claim_to_be_the_gui(client):
    # The override is keyed on the request source, so the source must come from
    # the caller's identity: a service principal saying UI_MANUAL is not the GUI.
    prod = register(client, "ov-spoof", "prod-for-us").json()
    test = make_test(client, R2, "ov-s")
    machine = {"X-Forwarded-Client-Id": "ci-sp"}
    body = {"test_ids": [test["id"]], "target_ids": [prod["id"]], "depth": "full",
            "request_source": "UI_MANUAL"}

    with_override = {**body, "override": {"target_name": "ov-spoof", "reason": "r", "confirmed": True}}
    assert client.post("/api/v1/runs", json=with_override, headers=machine).status_code == 403

    run_id = client.post("/api/v1/runs", json=body, headers=machine).json()["run_id"]
    resource = client.get(f"/api/v1/runs/{run_id}").json()
    assert resource["request_source"] == "API"
    assert resource["requested_by"] == "ci-sp"


def test_the_forwarded_identity_is_the_requester_and_the_audit_actor(client):
    headers = {"X-Forwarded-Email": "eng@example.com"}
    target = register(client, "who-1", "read-only").json()
    created = client.post("/api/v1/tests/script?actor=spoofed", headers=headers, json={
        "name": "who-t", "script": 'with check("lists clusters"):\n    w.clusters.list()\n'})
    assert created.status_code == 201, created.text
    run_id = client.post("/api/v1/runs", headers=headers, json={
        "target_ids": [target["id"]], "requested_by": "ui-user"}).json()["run_id"]

    assert client.get(f"/api/v1/runs/{run_id}").json()["requested_by"] == "eng@example.com"
    actions = {e["action"] for e in client.get("/api/v1/audit", params={"actor": "eng@example.com"}).json()}
    assert "test.create" in actions
    assert not client.get("/api/v1/audit", params={"actor": "spoofed"}).json()


# ---------------- 4. R3 global wall ----------------
def test_r3_is_refused_regardless_of_any_human_capability(client):
    # The R3-never floor is absolute: a blocklisted (catastrophic) call can
    # never even be *saved* in a Test script, so no target, ceremony, or override
    # can ever route it. Authoring one is refused up front.
    resp = client.post("/api/v1/tests/script", json={
        "name": "drops a metastore", "script": 'with check("x"):\n    w.metastores.delete(id="m")\n'})
    assert resp.status_code == 422
    assert any("blocklist" in e["message"] for e in resp.json()["detail"]["analysis"]["errors"])
    assert "drops a metastore" not in {t["name"] for t in client.get("/api/v1/tests").json()}

    # And the gate is the belt-and-braces floor beneath authoring: a blocklisted
    # op evaluated directly is BLOCKED even on the widest class with a human
    # override for the elevated tier.
    from app.core.enums import RequestSource, RunStatus, SafetyClass
    from app.services.safety import SafetyOverride, evaluate_operation_safety

    decision = evaluate_operation_safety(
        {"service": "metastores", "method": "delete"},
        SafetyClass.SANDBOX,
        override=SafetyOverride(target_name="r3", reason="r"),
        request_source=RequestSource.UI_MANUAL, target_name="r3")
    assert decision.status == RunStatus.BLOCKED_BY_SAFETY
    assert decision.layer == "blocklist"


# ---------------- 5. host-identity actor ----------------
def test_actor_is_the_host_app_identity_from_forwarded_headers(client):
    # Databricks Apps forward the authenticated human's identity; the audit actor
    # is that identity, not a client-supplied value.
    headers = {"X-Forwarded-Email": "sa@example.com"}
    register(client, "hi-1", "read-only", headers=headers)
    events = client.get("/api/v1/audit", params={"actor": "sa@example.com"}).json()
    assert events and all(e["actor"] == "sa@example.com" for e in events)
    assert all(e["actor_kind"] == "user" for e in events)
