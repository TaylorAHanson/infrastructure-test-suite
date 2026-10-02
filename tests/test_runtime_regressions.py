"""Regressions for the bugs the post-apply review found in the runtime.

A counterfactual that passes on any failure, a ``Wait`` that crashes the
evidence serializer, a SQL permission error that isn't a denial, and checks
that look like they assert something when they don't.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.enums import Depth, SafetyClass
from app.script_runtime import demo_workspace
from app.script_runtime.analysis import analyze
from app.script_runtime.runner import RunContext, run_script

from helpers import script_test, target_id


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    from app.services import run_service

    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


def _run(script, *, tags=None, params=None, safety_class=SafetyClass.READ_ONLY, depth=Depth.FULL):
    target = SimpleNamespace(id="t1", name="t", workspace_name="ws", host="https://x",
                             safety_class=safety_class, tags=tags or {}, assumable_roles=[],
                             identity_handle=None)
    return run_script(script, RunContext(target=target, depth=depth, demo_mode=True, params=params or {}))


def _fail_calls(monkeypatch, op: str, error: dict):
    """Make the demo workspace answer ``op`` (``service.method``) with ``error``."""
    original = demo_workspace.DemoWorkspace.respond

    def respond(self, kind, msg):
        if f"{msg.get('service')}.{msg.get('method')}" == op:
            return None, error
        return original(self, kind, msg)

    monkeypatch.setattr(demo_workspace.DemoWorkspace, "respond", respond)


# ---------------------------------------------------------------------------
# Counterfactuals: only the denial asked for counts
# ---------------------------------------------------------------------------
_DENIED = {"kind": "permission_denied", "message": "nope", "status": 403}
_UNAUTHENTICATED = {"kind": "unauthenticated", "message": "invalid token", "status": 401}
_DNS = {"kind": "connect", "message": "name resolution failed", "stage": "RESOLVE", "bucket": "DNS_FAILURE"}
_REFUSED = {"kind": "connect", "message": "connection refused", "stage": "CONNECT", "bucket": "NETWORK_POLICY_DENY"}
_TIMEOUT = {"kind": "connect", "message": "timed out", "stage": "CONNECT", "bucket": "UPSTREAM_TIMEOUT"}
_API = {"kind": "api", "message": "INVALID_STATE", "status": 400}


@pytest.mark.parametrize("at, error, ok", [
    ("AUTHORIZE", _DENIED, True),
    ("AUTHORIZE", _DNS, False),
    ("AUTHORIZE", _UNAUTHENTICATED, False),
    ("AUTHORIZE", _API, False),
    ("CONNECT", _REFUSED, True),
    ("CONNECT", _DNS, True),
    ("CONNECT", _TIMEOUT, False),
    ("CONNECT", _DENIED, False),
    ("OPERATE", _API, True),
    ("OPERATE", _DENIED, True),
    ("OPERATE", _UNAUTHENTICATED, False),
    ("OPERATE", _DNS, False),
])
def test_a_counterfactual_passes_only_on_the_denial_it_names(monkeypatch, at, error, ok):
    _fail_calls(monkeypatch, "clusters.get", error)
    res = _run(f'with expect_deny("refused", at="{at}"):\n    w.clusters.get(cluster_id="x")\n')
    assert (res.check("refused").outcome == "pass") is ok, res.check("refused").message


def _run_counterfactual(client, at):
    test = script_test(client, f'with expect_deny("denied at {at}", at="{at}"):\n'
                               '    sql("SELECT * FROM main.health.forbidden")\n', f"cf {at}")
    run_id = client.post("/api/v1/runs", json={"test_ids": [test["id"]], "target_ids": [target_id(client, "read-only")],
                                               "depth": "full"}).json()["run_id"]
    (cr,) = client.get(f"/api/v1/runs/{run_id}").json()["run_ids"]
    return client.get(f"/api/v1/runs/{cr}").json()


def test_a_policy_denial_does_not_satisfy_a_counterfactual_that_expected_a_network_block(client):
    assert _run_counterfactual(client, "AUTHORIZE")["verdict"] == "PASS"
    detail = _run_counterfactual(client, "CONNECT")
    assert detail["verdict"] == "FAIL"
    assert "denied at AUTHORIZE, expected CONNECT" in detail["message"]


@pytest.mark.parametrize("error", [
    {"kind": "unauthenticated", "message": "invalid token", "status": 401},
    {"kind": "connect", "message": "name resolution failed", "stage": "RESOLVE", "bucket": "DNS_FAILURE"},
    {"kind": "not_found", "message": "no such cluster", "status": 404},
])
def test_a_script_counterfactual_is_not_satisfied_by_an_outage_or_bad_identity(monkeypatch, error):
    _fail_calls(monkeypatch, "clusters.get", error)
    res = _run('with expect_deny("refused"):\n    w.clusters.get(cluster_id="x")\n')
    assert res.check("refused").outcome == "fail", res.check("refused").message


def test_a_script_counterfactual_passes_on_a_permission_denial(monkeypatch):
    _fail_calls(monkeypatch, "clusters.get", {"kind": "permission_denied", "message": "nope", "status": 403})
    res = _run('with expect_deny("refused"):\n    w.clusters.get(cluster_id="x")\n')
    assert res.check("refused").outcome == "pass"


def test_an_operate_counterfactual_accepts_an_api_error_but_not_a_401(monkeypatch):
    script = 'with expect_deny("refused", at="OPERATE"):\n    w.clusters.get(cluster_id="x")\n'
    _fail_calls(monkeypatch, "clusters.get", {"kind": "api", "message": "INVALID_STATE", "status": 400})
    assert _run(script).check("refused").outcome == "pass"
    _fail_calls(monkeypatch, "clusters.get", {"kind": "unauthenticated", "message": "expired", "status": 401})
    assert _run(script).check("refused").outcome == "fail"


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
def test_reading_an_ungranted_table_is_a_permission_denial():
    res = _run('with expect_deny("refused"):\n    sql("SELECT * FROM c.health.forbidden")\n'
               'with check("raises PermissionDenied"):\n'
               '    try:\n        sql("SELECT * FROM c.health.forbidden")\n'
               '    except PermissionDenied as e:\n        assert "INSUFFICIENT_PERMISSIONS" in str(e)\n'
               '    else:\n        raise AssertionError("no denial")\n')
    assert res.check("refused").outcome == "pass", res.check("refused").message
    assert res.check("raises PermissionDenied").outcome == "pass", res.check("raises PermissionDenied").message


def test_a_statement_still_running_after_the_wait_is_polled_to_completion(monkeypatch):
    original = demo_workspace.DemoWorkspace.respond

    def respond(self, kind, msg):
        if msg.get("method") == "execute_statement":
            return {"statement_id": "s1", "status": {"state": "PENDING"}}, None
        if msg.get("method") == "get_statement":
            return {"statement_id": "s1", "status": {"state": "SUCCEEDED"},
                    "manifest": {"schema": {"columns": [{"name": "ok"}]}},
                    "result": {"data_array": [["1"]]}}, None
        return original(self, kind, msg)

    monkeypatch.setattr(demo_workspace.DemoWorkspace, "respond", respond)
    res = _run('with check("polled"):\n    assert sql("SELECT 1 AS ok", warehouse_id="wh", max_wait=8) == [{"ok": "1"}]\n')
    assert res.check("polled").outcome == "pass", res.check("polled").message


def test_sql_uses_the_targets_warehouse_tag(monkeypatch):
    original, seen = demo_workspace.DemoWorkspace.respond, []

    def respond(self, kind, msg):
        if msg.get("method") == "execute_statement":
            seen.append((msg.get("kwargs") or {}).get("warehouse_id"))
        return original(self, kind, msg)

    monkeypatch.setattr(demo_workspace.DemoWorkspace, "respond", respond)
    res = _run('with check("w"):\n    sql("SELECT 1")\n', tags={"warehouse_id": "tagged-wh"})
    assert res.check("w").outcome == "pass" and seen == ["tagged-wh"]


def test_an_f_string_statement_is_tiered_by_its_literal_text():
    out = analyze('t = "x"\nwith check("w"):\n    sql(f"CREATE OR REPLACE TABLE {t} AS SELECT 1")\n    assert True\n')
    assert out["max_risk_tier"] == "R1"
    out = analyze('t = "x"\nwith check("r"):\n    sql(f"SELECT * FROM {t}")\n    assert True\n')
    assert out["max_risk_tier"] == "R0"


# ---------------------------------------------------------------------------
# skip(), setting(), and results
# ---------------------------------------------------------------------------
def test_setting_prefers_a_run_param_then_a_target_tag_then_the_default():
    script = ('with check("s"):\n'
              '    record("a", setting("a", "d"))\n    record("b", setting("b", "d"))\n'
              '    record("c", setting("c", "d"))\n')
    res = _run(script, tags={"a": "tag", "b": "tag"}, params={"a": "param"})
    assert res.check("s").evidence == {"a": "param", "b": "tag", "c": "d"}


def test_skip_ends_a_check_as_skipped_without_failing_the_script():
    res = _run('with check("n/a"):\n    skip("no classic warehouse configured")\n'
               'with check("next"):\n    assert True\n')
    assert res.check("n/a").outcome == "skipped"
    assert "no classic warehouse" in res.check("n/a").message
    assert res.check("next").outcome == "pass"


def test_results_answer_snake_case_for_camel_case_keys():
    from app.script_runtime.sandbox.validation import Obj

    me = Obj({"userName": "sp@example.com", "active": True})
    assert me.user_name == "sp@example.com" and me.active is True
    assert me.missing is None


# ---------------------------------------------------------------------------
# SDK plumbing
# ---------------------------------------------------------------------------
def test_a_long_running_operation_serializes_without_raising():
    from databricks.sdk.service._internal import Wait
    from databricks.sdk.service.jobs import SubmitRunResponse

    from app.script_runtime.sdk_catalog import to_jsonable

    wait = Wait(lambda **_: None, response=SubmitRunResponse(run_id=42), run_id=42)
    assert to_jsonable(wait) == {"run_id": 42}


# ---------------------------------------------------------------------------
# Checks say what they don't prove
# ---------------------------------------------------------------------------
def test_analysis_warns_on_a_check_with_nothing_to_prove():
    out = analyze('with check("reachable"):\n    w.clusters.list()\n'
                  'with check("proves"):\n    assert w.clusters.list() is not None\n')
    warned = [w["message"] for w in out["warnings"] if "has no assert" in w["message"]]
    assert warned == ["Check 'reachable' has no assert, so it passes whenever its calls don't raise. "
                      "Assert on what the result should contain."]


# ---------------------------------------------------------------------------
# A quick run doesn't judge what it didn't do
# ---------------------------------------------------------------------------
def test_a_quick_run_skips_a_mutating_check(client):
    test = script_test(client, 'with check("creates"):\n'
                               '    w.clusters.create(spark_version="15.4.x-scala2.12", cluster_name="t")\n', "quick")
    run_id = client.post("/api/v1/runs", json={"test_ids": [test["id"]],
                                               "target_ids": [target_id(client, "sandbox")]}).json()["run_id"]
    resource = client.get(f"/api/v1/runs/{run_id}").json()
    (cr,) = resource["run_ids"]
    detail = client.get(f"/api/v1/runs/{cr}").json()
    assert detail["verdict"] == "SKIPPED" and "quick runs only make read calls" in detail["message"]
    assert resource["verdict"] == "SKIPPED" and resource["functional"] is None


def test_a_safety_block_does_not_read_as_broken_connectivity(client):
    tst = script_test(client, 'with check("over-cap delete"):\n    w.clusters.delete(cluster_id="x")\n', "over-cap")
    run_id = client.post("/api/v1/runs", json={"test_ids": [tst["id"]], "target_ids": [target_id(client, "read-only")],
                                               "depth": "full"}).json()["run_id"]
    resource = client.get(f"/api/v1/runs/{run_id}").json()
    assert resource["verdict"] == "BLOCKED_BY_SAFETY"
    assert resource["connectable"] is None
