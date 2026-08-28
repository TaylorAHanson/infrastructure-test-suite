"""HTTP API seam for the audit read/filter/export surface (spec #36, ADR-0014).

Drives the real contract through ``TestClient`` + SQLite. Asserts observable
behaviour: config-plane mutations are recorded and runs are not; downgrades are
flagged and the override carries its ceremony reason; records filter by
target · actor · event type and export; and there is **no** API path that edits
or deletes an audit record (append-only).
"""
from __future__ import annotations


# --------------------------------- helpers ---------------------------------
def register_target(client, name, safety_class, *, actor=None,
                    identity_handle="kv/sp", roles=None):
    q = f"?actor={actor}" if actor else ""
    return client.post(f"/api/v1/targets{q}", json={
        "name": name, "workspace_name": name, "safety_class": safety_class,
        "identity_handle": identity_handle, "assumable_roles": roles or [],
    })


def make_check(client, service, method):
    return client.post("/api/v1/checks", json={
        "display_name": f"{service}.{method}",
        "operation": {"service": service, "method": method},
        "handler_key": "databricks_sdk", "assertion": "the operation succeeds",
    }).json()


def make_test(client, check_id, name):
    return client.post("/api/v1/tests", json={"name": name, "check_ids": [check_id]}).json()


def submit_run(client, test_id, target_id, *, request_source="API", override=None):
    body = {"test_ids": [test_id], "target_ids": [target_id], "depth": "full",
            "request_source": request_source}
    if override is not None:
        body["override"] = override
    return client.post("/api/v1/runs", json=body).json()["run_id"]


def audit(client, **params):
    return client.get("/api/v1/audit", params=params).json()


def actions(events):
    return {e["action"] for e in events}


# -------------------- config-plane recorded, runs excluded -------------------
def test_config_plane_mutations_recorded_runs_are_not(client):
    tgt = register_target(client, "ws-ro", "read-only").json()
    test = make_test(client, make_check(client, "jobs", "list")["id"], "ro-list")
    # A real run: this emits a run.trigger event, which must NOT surface here.
    submit_run(client, test["id"], tgt["id"])

    events = audit(client)
    seen = actions(events)
    # Config-plane mutations are present…
    assert "target.register" in seen
    assert "test.create" in seen
    # …and runs are not (neither the trigger nor any run-time block).
    assert "run.trigger" not in seen
    assert "safety.block" not in seen
    assert all(e["entity_type"] != "run_request" for e in events)


def test_blocked_run_does_not_appear_in_audit_view(client):
    ro = register_target(client, "ro-block", "read-only").json()
    # An R1 mutation on a read-only target is blocked — a run-plane event, not audit.
    test = make_test(client, make_check(client, "clusters", "create")["id"], "ro-create")
    submit_run(client, test["id"], ro["id"])
    assert "safety.block" not in actions(audit(client))


# ------------------------------- downgrade flag ------------------------------
def test_downgrade_is_flagged_in_audit(client):
    tgt = register_target(client, "to-downgrade", "prod-for-us").json()
    # prod-for-us (R1 cap) → sandbox (R2 cap) widens the blast radius: a downgrade.
    client.patch(f"/api/v1/targets/{tgt['id']}", json={"safety_class": "sandbox"})

    events = audit(client, event_type="target.safety_class.change")
    assert len(events) == 1
    assert events[0]["before"]["downgrade"] is True
    assert "DOWNGRADE" in events[0]["summary"]


# --------------------------- override ceremony reason ------------------------
def test_override_records_its_ceremony_reason(client):
    prod = register_target(client, "prod-ov", "prod-for-us").json()
    test = make_test(client, make_check(client, "clusters", "delete")["id"], "ov-del")
    override = {"target_name": "prod-ov", "reason": "incident-4821 remediation",
                "confirmed": True}
    submit_run(client, test["id"], prod["id"], request_source="UI_MANUAL", override=override)

    events = audit(client, event_type="safety.override")
    assert len(events) == 1
    assert events[0]["after"]["override_reason"] == "incident-4821 remediation"


# ------------------------------- deregister ----------------------------------
def test_deregister_is_audited(client):
    tgt = register_target(client, "to-drop", "read-only").json()
    resp = client.delete(f"/api/v1/targets/{tgt['id']}")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    events = audit(client, target=tgt["id"], event_type="target.deregister")
    assert len(events) == 1
    assert events[0]["entity_id"] == tgt["id"]


# ------------------------ Test create/edit → version pointer -----------------
def test_test_create_and_edit_recorded_as_version_pointer(client):
    check = make_check(client, "jobs", "list")
    test = make_test(client, check["id"], "vp-test")
    client.patch(f"/api/v1/tests/{test['id']}", json={"description": "edited"})

    created = audit(client, event_type="test.create")[0]
    assert created["after"] == {"entity_type": "test", "entity_id": test["id"], "version": 1}

    edited = audit(client, event_type="test.update")[0]
    # A pointer to the new version, not a duplicated diff of the Test body.
    assert edited["after"]["version"] == 2
    assert edited["before"]["version"] == 1
    assert set(edited["after"]) == {"entity_type", "entity_id", "version"}


# --------------------------------- filtering ---------------------------------
def test_filter_by_target_actor_and_event_type(client):
    a = register_target(client, "ws-a", "read-only", actor="alice").json()
    register_target(client, "ws-b", "sandbox", actor="bob")

    # By target: only ws-a's own events.
    by_target = audit(client, target=a["id"])
    assert by_target and all(e["entity_id"] == a["id"] for e in by_target)

    # By actor: only bob's writes.
    by_actor = audit(client, actor="bob")
    assert by_actor and all(e["actor"] == "bob" for e in by_actor)

    # By event type: only registrations, and at least the two we made.
    by_type = audit(client, event_type="target.register")
    assert len(by_type) >= 2 and all(e["action"] == "target.register" for e in by_type)


# ---------------------------------- export -----------------------------------
def test_export_csv_is_filtered_and_forensically_complete(client):
    tgt = register_target(client, "ws-exp", "prod-for-us", actor="carol").json()
    # A downgrade so before/after carry the flag the export must preserve.
    client.patch(f"/api/v1/targets/{tgt['id']}?actor=carol", json={"safety_class": "sandbox"})

    resp = client.get("/api/v1/audit/export", params={"format": "csv", "actor": "carol"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    header = resp.text.splitlines()[0]
    assert "actor" in header and "before" in header and "after" in header
    assert "carol" in resp.text
    # The downgrade flag lives in before→after and must survive the CSV export.
    assert "downgrade" in resp.text


def test_export_json(client):
    register_target(client, "ws-expj", "read-only", actor="dan")
    resp = client.get("/api/v1/audit/export", params={"format": "json", "actor": "dan"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    rows = resp.json()
    assert rows and all(r["actor"] == "dan" for r in rows)
    # Full records, including the before→after payload (ADR-0014 §3).
    assert all("before" in r and "after" in r for r in rows)


# ------------------------------- append-only ---------------------------------
def test_audit_is_append_only_no_edit_or_delete_path(client):
    register_target(client, "ws-immut", "read-only")
    before = audit(client)
    assert before
    victim = before[0]["id"]

    # No write verb is exposed on the audit surface.
    assert client.post("/api/v1/audit", json={"actor": "x"}).status_code == 405
    assert client.delete("/api/v1/audit").status_code == 405
    assert client.patch("/api/v1/audit", json={}).status_code == 405
    # And there is no per-record mutate route at all.
    assert client.delete(f"/api/v1/audit/{victim}").status_code in (404, 405)

    # Nothing was removed.
    after = audit(client)
    assert len(after) == len(before)
