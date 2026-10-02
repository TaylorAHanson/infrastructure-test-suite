"""HTTP API seam: a Test's version history captures its Checks' content (#118).

Pin, don't copy: each member in a Test snapshot records the member Check's
``check_version`` beside its ``check_id``. Editing a check in the Test's script
is a new Test version; rolling the Test back restores the script and the pinned
Check content. Check ids never change, so run history keyed by ``check_id``
stays continuous.
"""
from __future__ import annotations

import pytest

from helpers import script_test, target_id

V1 = 'with check("lists jobs"):\n    w.jobs.list()\n'
#: The same check (same name, so the same Check id), now a counterfactual.
V2 = 'with expect_deny("lists jobs"):\n    w.jobs.list()\n'


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    from app.services import run_service

    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


def _test(client, tid):
    return client.get(f"/api/v1/tests/{tid}").json()


def _versions(client, tid):
    return client.get(f"/api/v1/tests/{tid}/versions").json()


def _first_check_id(client, tid):
    return _test(client, tid)["members"][0]["check_id"]


def _save(client, tid, script, **params):
    resp = client.put(f"/api/v1/tests/{tid}/script", json={"script": script}, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_editing_a_check_in_the_script_is_a_new_test_version(client):
    tid = script_test(client, V1, "versioned", active=False)["id"]
    cid = _first_check_id(client, tid)
    before = client.get(f"/api/v1/checks/{cid}").json()

    assert _save(client, tid, V2)["version"] == 2

    check = client.get(f"/api/v1/checks/{cid}").json()
    assert check["id"] == cid                          # id stable: run history continuity
    assert check["expectation"] == "DENY"
    assert check["version"] == before["version"] + 1

    latest = _versions(client, tid)[-1]
    assert latest["version"] == 2
    pinned = {m["check_id"]: m["check_version"] for m in latest["snapshot"]["members"]}
    assert pinned[cid] == check["version"]


def _run_expectations(client, tid):
    """Run the Test and return each fan-out CheckRun's ``(check_id, expectation)``."""
    req = client.post("/api/v1/runs", json={"test_ids": [tid], "target_ids": [target_id(client, "read-only")],
                                            "requested_by": "test"}).json()
    run_ids = client.get(f"/api/v1/runs/{req['run_id']}").json()["run_ids"]
    runs = [client.get(f"/api/v1/runs/{r}").json() for r in run_ids]
    return {(r["check_id"], r["expectation"]) for r in runs}


def test_rolling_a_test_back_restores_its_script_and_checks_content(client):
    test = script_test(client, V1, "rolled back")
    tid, cid = test["id"], test["members"][0]["check_id"]
    original = client.get(f"/api/v1/checks/{cid}").json()
    assert original["expectation"] == "PASS"

    _save(client, tid, V2)
    assert (cid, "DENY") in _run_expectations(client, tid)

    resp = client.post(f"/api/v1/tests/{tid}/rollback/{test['version']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == test["version"] + 2
    assert resp.json()["script"] == V1

    restored = client.get(f"/api/v1/checks/{cid}").json()
    assert restored["id"] == cid
    assert restored["expectation"] == "PASS"
    assert (cid, "PASS") in _run_expectations(client, tid)   # the next run uses restored content

    latest = _versions(client, tid)[-1]["snapshot"]["members"]
    assert {m["check_id"]: m["check_version"] for m in latest}[cid] == restored["version"]


def test_a_version_without_a_script_is_refused_and_nothing_changes(client):
    # A Test from before scripts: its history holds a version with no script,
    # which can't run, so restoring it is refused rather than half-applied.
    from app.db.session import session_scope
    from app.models import Test
    from app.services import pinned_versions

    with session_scope() as s:
        legacy = Test(name="legacy", description="structured")
        s.add(legacy)
        s.commit()
        pinned_versions.record_test_version(s, legacy, actor="seed")
        tid = legacy.id
    saved = _save(client, tid, V1)

    resp = client.post(f"/api/v1/tests/{tid}/rollback/1")
    assert resp.status_code == 409
    assert "no script" in resp.json()["detail"]
    after = _test(client, tid)
    assert after["version"] == saved["version"] and after["script"] == V1
    assert after["members"] == saved["members"]


def test_rolling_an_accepted_test_back_to_its_draft_version_keeps_it_active(client):
    draft = script_test(client, V1, "drafted", active=False, description="v1")
    tid = draft["id"]
    assert client.post(f"/api/v1/tests/{tid}/accept").status_code == 200
    client.patch(f"/api/v1/tests/{tid}", json={"description": "edited after accept"})
    assert _test(client, tid)["status"] == "active"

    resp = client.post(f"/api/v1/tests/{tid}/rollback/1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["description"] == "v1"                 # content restored
    assert body["status"] == "active"                  # lifecycle kept: still runs
    assert body["enabled"] is True


def test_rolling_back_keeps_the_current_enabled_flag(client):
    tid = script_test(client, V1, "toggled", active=False, description="v1")["id"]
    client.patch(f"/api/v1/tests/{tid}", json={"description": "v2", "enabled": False})

    body = client.post(f"/api/v1/tests/{tid}/rollback/1").json()
    assert body["description"] == "v1"
    assert body["enabled"] is False


def test_the_api_exposes_which_other_tests_share_a_check(client):
    # A script's checks are its own, but Tests from before scripts could share
    # a Check; reads still say which other Tests list it.
    from app.db.session import session_scope
    from app.schemas.schemas import TestCheckIn
    from app.services import pinned_versions

    one = script_test(client, 'with check("shared"):\n    pass\nwith check("own"):\n    pass\n', "one")
    t1, (shared, own) = one["id"], [m["check_id"] for m in one["members"]]
    t2 = script_test(client, 'with check("x"):\n    pass\n', "two")["id"]
    with session_scope() as s:
        pinned_versions.set_members(s, t2, [TestCheckIn(check_id=shared)])
        s.commit()

    members = {m["check_id"]: m["shared_with"] for m in _test(client, t1)["members"]}
    assert members == {shared: [t2], own: []}
    assert _test(client, t2)["members"][0]["shared_with"] == [t1]
    # Read-time context, not Test content: never written into the version history.
    assert all("shared_with" not in m for m in _versions(client, t1)[-1]["snapshot"]["members"])


def test_a_refused_script_edit_persists_nothing(client):
    test = script_test(client, V1, "guarded", active=False)
    tid, cid = test["id"], test["members"][0]["check_id"]
    before_check = client.get(f"/api/v1/checks/{cid}").json()

    resp = client.put(f"/api/v1/tests/{tid}/script", json={
        "script": 'with check("lists jobs"):\n    w.metastores.delete(id="m")\n', "description": "should not land"})
    assert resp.status_code == 422
    assert any("blocklist" in e["message"] for e in resp.json()["detail"]["analysis"]["errors"])

    after = _test(client, tid)
    assert after["version"] == 1 and after["description"] != "should not land" and after["script"] == V1
    assert client.get(f"/api/v1/checks/{cid}").json() == before_check


def test_a_script_edit_is_audited_as_a_test_update_pointer(client):
    tid = script_test(client, V1, "audited", active=False)["id"]
    _save(client, tid, V2, actor="erin")

    updates = client.get("/api/v1/audit", params={"actor": "erin", "event_type": "test.update"}).json()
    assert len(updates) == 1
    assert updates[0]["before"] == {"entity_type": "test", "entity_id": tid, "version": 1}
    assert updates[0]["after"] == {"entity_type": "test", "entity_id": tid, "version": 2}
