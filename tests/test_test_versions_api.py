"""HTTP API seam: a Test's version history captures its Checks' content (#118).

Pin, don't copy: each member in a Test snapshot records the member Check's
``check_version`` beside its ``check_id``. Editing a Check *through its Test*
is a new Test version; rolling the Test back restores the pinned Check content.
Check ids never change, so run history keyed by ``check_id`` stays continuous.
"""
from __future__ import annotations

from helpers import test_id as _test_id

SQL = "SQL Warehouse validation"


def _test(client, tid):
    return client.get(f"/api/v1/tests/{tid}").json()


def _versions(client, tid):
    return client.get(f"/api/v1/tests/{tid}/versions").json()


def _first_check_id(client, tid):
    return _test(client, tid)["members"][0]["check_id"]


def test_editing_a_check_through_its_test_is_a_new_test_version(client):
    tid = _test_id(client, SQL)
    cid = _first_check_id(client, tid)
    before = client.get(f"/api/v1/checks/{cid}").json()

    resp = client.patch(f"/api/v1/tests/{tid}", json={
        "check_edits": [{"check_id": cid, "assertion": "the operation is denied"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 2

    check = client.get(f"/api/v1/checks/{cid}").json()
    assert check["id"] == cid                          # id stable: run history continuity
    assert check["assertion"] == "the operation is denied"
    assert check["version"] == before["version"] + 1

    latest = _versions(client, tid)[-1]
    assert latest["version"] == 2
    pinned = {m["check_id"]: m["check_version"] for m in latest["snapshot"]["members"]}
    assert pinned[cid] == check["version"]


def _run_expectations(client, tid):
    """Run the Test and return each fan-out CheckRun's ``(check_id, expectation)``."""
    req = client.post("/api/v1/runs", json={"test_ids": [tid], "requested_by": "test"}).json()
    run_ids = client.get(f"/api/v1/runs/{req['run_id']}").json()["run_ids"]
    runs = [client.get(f"/api/v1/runs/{r}").json() for r in run_ids]
    return {(r["check_id"], r["expectation"]) for r in runs}


def test_rolling_a_test_back_restores_its_checks_content(client):
    tid = _test_id(client, SQL)
    cid = _first_check_id(client, tid)
    original = client.get(f"/api/v1/checks/{cid}").json()
    assert original["expectation"] == "PASS"

    client.patch(f"/api/v1/tests/{tid}", json={
        "check_edits": [{"check_id": cid, "assertion": "the operation is denied"}]})
    assert (cid, "DENY") in _run_expectations(client, tid)

    resp = client.post(f"/api/v1/tests/{tid}/rollback/1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 3

    restored = client.get(f"/api/v1/checks/{cid}").json()
    assert restored["id"] == cid
    assert restored["assertion"] == original["assertion"]
    assert restored["expectation"] == "PASS"
    assert (cid, "PASS") in _run_expectations(client, tid)   # the next run uses restored content

    latest = _versions(client, tid)[-1]["snapshot"]["members"]
    assert {m["check_id"]: m["check_version"] for m in latest}[cid] == restored["version"]


def _strip_pins(tid, version):
    """Rewrite a stored Test snapshot into its pre-#118 shape (members without pins).

    There is no API that writes legacy-shaped history any more, so the fixture
    reaches into the store the way an upgraded install's old rows would look.
    """
    from app.db.session import session_scope
    from app.services import versioning

    with session_scope() as s:
        row = versioning.get_version(s, "test", tid, version)
        snap = dict(row.snapshot)
        snap["members"] = [{k: v for k, v in m.items() if k != "check_version"}
                           for m in snap["members"]]
        row.snapshot = snap
        s.add(row)
        s.commit()


def test_a_snapshot_without_pins_rolls_back_membership_and_fields_only(client):
    a = client.post("/api/v1/checks", json={
        "display_name": "legacy a", "operation": {"service": "jobs", "method": "list"},
        "assertion": "the operation succeeds"}).json()
    b = client.post("/api/v1/checks", json={
        "display_name": "legacy b", "operation": {"service": "clusters", "method": "list"},
        "assertion": "the operation succeeds"}).json()
    tid = client.post("/api/v1/tests", json={"name": "legacy", "check_ids": [a["id"]]}).json()["id"]
    _strip_pins(tid, 1)

    client.patch(f"/api/v1/tests/{tid}", json={
        "description": "edited", "check_ids": [a["id"], b["id"]],
        "check_edits": [{"check_id": a["id"], "display_name": "legacy a, edited"}]})

    resp = client.post(f"/api/v1/tests/{tid}/rollback/1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["description"] == ""
    assert [m["check_id"] for m in body["members"]] == [a["id"]]
    # No pin to restore to: the Check keeps its current content, as before #118.
    assert client.get(f"/api/v1/checks/{a['id']}").json()["display_name"] == "legacy a, edited"


def test_rolling_an_accepted_test_back_to_its_draft_version_keeps_it_active(client):
    from app.db.session import session_scope
    from app.models import Test
    from app.services import pinned_versions
    from app.services.assistant import accept_test

    with session_scope() as s:
        draft = Test(name="drafted", description="v1", status="draft")
        s.add(draft)
        s.commit()
        pinned_versions.record_test_version(s, draft, actor="assistant")
        tid = draft.id
        accept_test(s, tid)
    client.patch(f"/api/v1/tests/{tid}", json={"description": "edited after accept"})
    assert _test(client, tid)["status"] == "active"

    resp = client.post(f"/api/v1/tests/{tid}/rollback/1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["description"] == "v1"                 # content restored
    assert body["status"] == "active"                  # lifecycle kept: still runs
    assert body["enabled"] is True


def test_rolling_back_keeps_the_current_enabled_flag(client):
    tid = client.post("/api/v1/tests", json={"name": "toggled", "description": "v1"}).json()["id"]
    client.patch(f"/api/v1/tests/{tid}", json={"description": "v2", "enabled": False})

    body = client.post(f"/api/v1/tests/{tid}/rollback/1").json()
    assert body["description"] == "v1"
    assert body["enabled"] is False


def test_the_api_exposes_which_other_tests_share_a_check(client):
    shared = client.post("/api/v1/checks", json={
        "display_name": "shared", "operation": {"service": "jobs", "method": "list"},
        "assertion": "the operation succeeds"}).json()["id"]
    own = client.post("/api/v1/checks", json={
        "display_name": "own", "operation": {"service": "clusters", "method": "list"},
        "assertion": "the operation succeeds"}).json()["id"]
    t1 = client.post("/api/v1/tests", json={"name": "one", "check_ids": [shared, own]}).json()["id"]
    t2 = client.post("/api/v1/tests", json={"name": "two", "check_ids": [shared]}).json()["id"]

    members = {m["check_id"]: m["shared_with"] for m in _test(client, t1)["members"]}
    assert members == {shared: [t2], own: []}
    assert _test(client, t2)["members"][0]["shared_with"] == [t1]
    # Read-time context, not Test content: never written into the version history.
    assert all("shared_with" not in m for m in _versions(client, t1)[-1]["snapshot"]["members"])


def test_a_refused_check_edit_through_a_test_persists_nothing(client):
    tid = _test_id(client, SQL)
    cid = _first_check_id(client, tid)
    before_check = client.get(f"/api/v1/checks/{cid}").json()

    resp = client.patch(f"/api/v1/tests/{tid}", json={
        "description": "should not land",
        "check_edits": [{"check_id": cid, "operation": {"service": "workspaces", "method": "delete"}}]})
    assert resp.status_code == 422
    assert "blocklisted" in resp.json()["detail"].lower()

    stranger = client.post("/api/v1/checks", json={
        "display_name": "not a member", "operation": {"service": "jobs", "method": "list"},
        "assertion": "the operation succeeds"}).json()["id"]
    resp = client.patch(f"/api/v1/tests/{tid}", json={
        "check_edits": [{"check_id": stranger, "display_name": "x"}]})
    assert resp.status_code == 422

    assert _test(client, tid)["version"] == 1 and _test(client, tid)["description"] != "should not land"
    assert client.get(f"/api/v1/checks/{cid}").json() == before_check


def test_a_check_edit_through_a_test_is_audited_as_a_test_update_pointer(client):
    tid = _test_id(client, SQL)
    cid = _first_check_id(client, tid)
    client.patch(f"/api/v1/tests/{tid}", json={
        "check_edits": [{"check_id": cid, "display_name": "SQL warehouse healthcheck (edited)"}]}, params={"actor": "erin"})

    updates = client.get("/api/v1/audit", params={"actor": "erin", "event_type": "test.update"}).json()
    assert len(updates) == 1
    assert updates[0]["before"] == {"entity_type": "test", "entity_id": tid, "version": 1}
    assert updates[0]["after"] == {"entity_type": "test", "entity_id": tid, "version": 2}
