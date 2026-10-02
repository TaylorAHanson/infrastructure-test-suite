"""Script Tests in the run engine: run params, workspace names, logs stored once,
the identity rule in a deployed app, bounded SDK timeouts, legacy Check reuse on
conversion, and bulk loads."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlmodel import select

from app.core.enums import Depth, RequestSource, SafetyClass
from app import models
from app.db.session import get_engine, session_scope
from app.models import CheckRun, RunRequest, StageOutcome, Target
from app.script_runtime.runner import RunContext, run_script
from app.services import run_service, script_tests

from helpers import target_id as _target


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


def _active_script_test(client, script, name="Engine script"):
    r = client.post("/api/v1/tests/script", json={"name": name, "script": script})
    assert r.status_code == 201, r.text
    test = r.json()
    assert client.post(f"/api/v1/tests/{test['id']}/accept").status_code == 200
    return test


def _check_runs(request_id):
    with session_scope() as s:
        return {r.check_id: r for r in s.exec(select(CheckRun).where(CheckRun.request_id == request_id)).all()}


def _by_name(client, request_id):
    runs = client.get("/api/v1/runs", params={"request_id": request_id}).json()
    return {r["check_name"]: r for r in runs}


def _unnamed_workspace_target():
    with session_scope() as s:
        target = Target(name="ws-without-a-name", workspace_name=None, safety_class=SafetyClass.READ_ONLY)
        s.add(target)
        s.commit()
        return target.id


# ------------------------------- params (S13) -------------------------------
def test_run_params_reach_the_script(client):
    test = _active_script_test(client, 'with check("reads params"):\n'
                                       '    record("raw", params.get("cluster"))\n'
                                       '    record("via_setting", setting("cluster", "fallback"))\n')
    r = client.post("/api/v1/runs", json={"test_ids": [test["id"]], "target_ids": [_target(client, "read-only")],
                                          "params": {"cluster": "c-42"}})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}").json()["params"] == {"cluster": "c-42"}
    evidence = _by_name(client, run_id)["reads params"]["evidence"]
    assert evidence["raw"] == "c-42" and evidence["via_setting"] == "c-42"


# ------------------------- workspace name fallback -------------------------
def test_check_runs_fall_back_to_the_target_name_for_their_workspace(client):
    test = _active_script_test(client, 'with check("one"):\n    pass\n')
    target = _unnamed_workspace_target()
    with session_scope() as s:
        req = RunRequest(test_ids=[test["id"]], target_ids=[target])
        s.add(req)
        s.commit()
        run_ids = [r.run_id for r in run_service.expand_request(s, req)]
        # A check the script produces that the expansion didn't know about.
        stored = s.get(models.Test, test["id"])
        stored.script += 'with check("added after expansion"):\n    pass\n'
        s.add(stored)
        s.commit()
        request_id = req.id
    script_tests.execute_script_group(run_ids)

    runs = _check_runs(request_id)
    assert len(runs) == 2
    assert {r.workspace_name for r in runs.values()} == {"ws-without-a-name"}


# ---------------------------- logs stored once ----------------------------
def test_script_logs_are_stored_once_per_group(client):
    test = _active_script_test(client, 'log("hello")\nprint("out")\n'
                                       'with check("a"):\n    w.current_user.me()\n'
                                       'with check("b"):\n    pass\n'
                                       'with check("c"):\n    pass\n')
    run_id = client.post("/api/v1/runs", json={"test_ids": [test["id"]],
                                               "target_ids": [_target(client, "read-only")]}).json()["run_id"]
    runs = list(_by_name(client, run_id).values())
    carriers = [r for r in runs if "logs" in r["evidence"]]
    assert len(carriers) == 1
    carrier = carriers[0]
    assert "script_output" in carrier["evidence"]
    assert all(r["evidence"]["script_logs_run_id"] == carrier["run_id"] for r in runs if r is not carrier)
    assert all("calls" in r["evidence"] for r in runs)
    with session_scope() as s:
        stages = s.exec(select(StageOutcome).where(
            StageOutcome.run_id.in_([r["run_id"] for r in runs]))).all()  # type: ignore[attr-defined]
    assert stages and not any("calls" in (o.evidence or {}) for o in stages)


# ---------------------------- identity rule (S2) ----------------------------
def _target_view(identity_handle=None):
    return SimpleNamespace(id="t1", name="unregistered-sp", workspace_name="ws", host="https://x",
                           safety_class=SafetyClass.READ_ONLY, tags={}, assumable_roles=[],
                           identity_handle=identity_handle)


def test_a_deployed_app_never_runs_a_script_as_its_own_identity(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_NAME", "validation-platform")
    monkeypatch.setattr(run_service, "_resolve_identity_token", lambda target, role: (None, None))
    fallback = []
    monkeypatch.setattr("app.core.config.get_workspace_client", lambda **kw: fallback.append(kw))

    target = _target_view()
    ctx = RunContext(target=target, depth=Depth.QUICK, request_source=RequestSource.API, demo_mode=False,
                     client_for=script_tests.client_factory(target))
    res = run_script('with check("me"):\n    w.current_user.me()\n', ctx).check("me")
    assert res.outcome != "pass" and res.stage == "AUTHENTICATE"
    assert "no identity handle" in res.message
    assert fallback == []


def test_locally_a_target_without_an_identity_uses_the_cli_profile(monkeypatch):
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    monkeypatch.setattr(run_service, "_resolve_identity_token", lambda target, role: (None, None))
    fallback = []
    monkeypatch.setattr("app.core.config.get_workspace_client", lambda **kw: fallback.append(kw) or "client")
    assert script_tests.client_factory(_target_view())(None) == "client"
    assert fallback == [{"http_timeout_seconds": 60, "retry_timeout_seconds": 120}]


# --------------------------- SDK timeouts (S12) ---------------------------
class _Fake:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_script_sdk_clients_have_bounded_timeouts(monkeypatch):
    import databricks.sdk
    import databricks.sdk.config

    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", _Fake)
    monkeypatch.setattr(databricks.sdk.config, "Config", _Fake)
    monkeypatch.setattr(run_service, "_resolve_identity_token", lambda target, role: ("tok", None))

    client = script_tests.client_factory(_target_view("scope/key"))(None)
    assert client.kwargs["config"].kwargs == {
        "host": "https://x", "token": "tok", "auth_type": "pat",
        "http_timeout_seconds": 60, "retry_timeout_seconds": 120}


def test_the_profile_client_passes_timeouts_through_the_sdk_config(monkeypatch):
    import databricks.sdk
    import databricks.sdk.config

    from app.core import config

    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", _Fake)
    monkeypatch.setattr(databricks.sdk.config, "Config", _Fake)
    client = config.get_workspace_client(http_timeout_seconds=7, retry_timeout_seconds=9)
    assert client.kwargs["config"].kwargs == {
        "http_timeout_seconds": 7, "retry_timeout_seconds": 9, "profile": config.settings.DATABRICKS_PROFILE}


# ------------------------ legacy Check reuse (C13) ------------------------
def test_converting_a_structured_test_keeps_its_check_ids(client):
    tests = client.get("/api/v1/tests").json()
    owners: dict[str, int] = {}
    for t in tests:
        for m in t["members"]:
            owners[m["check_id"]] = owners.get(m["check_id"], 0) + 1
    test = next(t for t in tests if not t.get("script") and t["members"]
                and all(owners[m["check_id"]] == 1 for m in t["members"]))
    before = [m["check_id"] for m in test["members"]]

    script = client.get(f"/api/v1/tests/{test['id']}/script").json()["script"]
    r = client.put(f"/api/v1/tests/{test['id']}/script", json={"script": script})
    assert r.status_code == 200, r.text
    assert [m["check_id"] for m in r.json()["members"]] == before
    checks = {c["id"]: c for c in client.get("/api/v1/checks").json()}
    assert {checks[cid]["handler_key"] for cid in before} == {"script"}


# ------------------------------ bulk loads ------------------------------
@contextmanager
def _counting_selects():
    statements: list[str] = []

    def on_execute(conn, cursor, statement, *args):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)


def _script_group_selects(client, n):
    script = "".join(f'with check("check {i}"):\n    w.current_user.me()\n' for i in range(n))
    test = _active_script_test(client, script, name=f"{n} checks")
    with session_scope() as s:
        req = RunRequest(test_ids=[test["id"]], target_ids=[_target(client, "read-only")])
        s.add(req)
        s.commit()
        run_ids = [r.run_id for r in run_service.expand_request(s, req)]
    assert len(run_ids) == n
    with _counting_selects() as selects:
        script_tests.execute_script_group(run_ids)
    return len(selects)


def test_a_script_group_loads_its_rows_in_bulk(client):
    assert _script_group_selects(client, 2) == _script_group_selects(client, 8)
