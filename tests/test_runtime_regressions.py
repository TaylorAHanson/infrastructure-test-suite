"""Regressions for the bugs the post-apply review found in the runtime.

A counterfactual that passes on any failure, a ``Wait`` that crashes the
evidence serializer, a SQL permission error that isn't a denial, a target token
the SDK quietly ignores, and converted checks that look like they assert
something when they don't.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.enums import Depth, SafetyClass
from app.script_runtime import demo_workspace
from app.script_runtime.analysis import analyze
from app.script_runtime.codegen import structured_to_script
from app.script_runtime.runner import RunContext, run_script
from app.services.run_service import denied_as_expected


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
def _failed(stage, bucket):
    return SimpleNamespace(stage=stage, failure_bucket=bucket)


@pytest.mark.parametrize("expected, stage, bucket, ok", [
    ("AUTHORIZE", "AUTHORIZE", "PERMISSION_DENIED", True),
    ("AUTHORIZE", "RESOLVE", "DNS_FAILURE", False),
    ("AUTHORIZE", "AUTHENTICATE", "AUTHENTICATION_FAILURE", False),
    ("AUTHORIZE", "OPERATE", "OPERATION_FAILED", False),
    ("CONNECT", "CONNECT", "UPSTREAM_TIMEOUT", True),
    ("CONNECT", "OPERATE", "NETWORK_POLICY_DENY", True),
    ("CONNECT", "AUTHORIZE", "PERMISSION_DENIED", False),
    ("OPERATE", "OPERATE", "OPERATION_FAILED", True),
    ("OPERATE", "AUTHORIZE", "PERMISSION_DENIED", True),
    ("OPERATE", "AUTHENTICATE", "AUTHENTICATION_FAILURE", False),
    ("OPERATE", "RESOLVE", "DNS_FAILURE", False),
])
def test_a_structured_counterfactual_passes_only_on_the_denial_it_names(expected, stage, bucket, ok):
    check = SimpleNamespace(expected_stage=expected)
    assert denied_as_expected(check, _failed(stage, bucket)) is ok


def _run_counterfactual(client, expected_stage):
    from helpers import target_id

    chk = client.post("/api/v1/checks", json={
        "display_name": f"denied at {expected_stage}",
        "operation": {"service": "clusters", "method": "create", "params": {"demo_outcome": "deny"}},
        "handler_key": "databricks_sdk", "assertion": "the operation is denied",
        "expected_stage": expected_stage}).json()
    tst = client.post("/api/v1/tests", json={
        "name": f"cf {expected_stage}", "members": [{"check_id": chk["id"], "kind": "counterfactual"}]}).json()
    run_id = client.post("/api/v1/runs", json={"test_ids": [tst["id"]], "target_ids": [target_id(client, "sandbox")],
                                               "depth": "full"}).json()["run_id"]
    (cr,) = client.get(f"/api/v1/runs/{run_id}").json()["run_ids"]
    return client.get(f"/api/v1/runs/{cr}").json()


def test_a_policy_denial_does_not_satisfy_a_counterfactual_that_expected_a_network_block(client):
    assert _run_counterfactual(client, "AUTHORIZE")["verdict"] == "PASS"
    detail = _run_counterfactual(client, "CONNECT")
    assert detail["verdict"] == "FAIL"
    assert "Expected a denial at CONNECT, but it failed at AUTHORIZE" in str(detail)


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

    from app.handlers.builtin.generic import _response_evidence
    from app.script_runtime.sdk_catalog import to_jsonable

    wait = Wait(lambda **_: None, response=SubmitRunResponse(run_id=42), run_id=42)
    assert to_jsonable(wait) == {"run_id": 42}
    assert _response_evidence(wait) == {"response": {"run_id": 42}}


def test_the_workspace_client_uses_the_targets_token_not_ambient_credentials(monkeypatch):
    import databricks.sdk

    from app.handlers.builtin import generic

    seen = {}
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda **kw: seen.update(kw) or object())
    generic._make_workspace_client("https://h", "tok")
    assert seen == {"host": "https://h", "token": "tok", "auth_type": "pat"}


# ---------------------------------------------------------------------------
# Converted structured tests say what they don't prove
# ---------------------------------------------------------------------------
def _check(**kw):
    base = dict(id="c1", display_name="lists warehouses", handler_key="databricks_sdk",
                operation={"service": "warehouses", "method": "list"}, compiled_predicate=None,
                assertion="the operation succeeds", expectation="PASS", evaluator="deterministic",
                expected_stage=None)
    return SimpleNamespace(**{**base, **kw})


def _convert(*checks):
    test = SimpleNamespace(name="T", description="")
    member = SimpleNamespace(run_as_role=None, kind="assertion")
    return structured_to_script(test, [(member, c) for c in checks])


def test_conversion_names_results_for_what_they_are():
    script = _convert(_check(), _check(id="c2", display_name="me", operation={"service": "current_user",
                                                                                "method": "me"}),
                      _check(id="c3", display_name="sql", handler_key="sql_warehouse",
                             operation={"service": "statement_execution", "method": "execute_statement",
                                        "params": {"statement": "SELECT 1"}}))
    assert "warehouses = w.warehouses.list()" in script
    assert "me = w.current_user.me()" in script
    assert "rows = sql('SELECT 1')" in script


def test_conversion_flags_a_check_that_only_required_success():
    script = _convert(_check())
    assert "TODO: the original check only required this call to succeed" in script
    out = analyze(script)
    assert out["ok"]
    assert any("has no assert" in w["message"] for w in out["warnings"])


def test_conversion_flags_an_assert_on_a_field_that_only_existed_in_demo_data():
    script = _convert(_check(operation={"service": "workspace_conf", "method": "get_status",
                                        "params": {"keys": "x", "demo_evidence": {"x": "true"}}},
                             compiled_predicate={"kind": "compare", "field": "x", "op": "==", "value": "true"}))
    assert "TODO: 'x' came from demo data" in script
    assert "assert workspace_conf.get('x') == 'true'" in script


def test_analysis_warns_on_a_check_with_nothing_to_prove():
    out = analyze('with check("reachable"):\n    w.clusters.list()\n'
                  'with check("proves"):\n    assert w.clusters.list() is not None\n')
    warned = [w["message"] for w in out["warnings"] if "has no assert" in w["message"]]
    assert warned == ["Check 'reachable' has no assert, so it passes whenever its calls don't raise. "
                      "Assert on what the result should contain."]


# ---------------------------------------------------------------------------
# Structured Checks against a real workspace: demo knobs are inert, and a
# quick run doesn't judge what it didn't do
# ---------------------------------------------------------------------------
def _live_ctx(params, *, token="tok", factory=None):
    from app.core.operations import Operation
    from app.handlers.base import HandlerContext

    return HandlerContext(run_id="r", operation=Operation.from_dict({"service": "clusters", "method": "create"}),
                          target=SimpleNamespace(host="https://h"), params=params, demo_mode=False, token=token,
                          workspace_client_factory=factory)


def test_a_demo_deny_param_does_not_deny_against_a_real_workspace():
    from app.core.enums import StageStatus
    from app.handlers.builtin.generic import DatabricksSdkHandler

    result = DatabricksSdkHandler().stage_authorize(_live_ctx({"demo_outcome": "deny"}))
    assert result.outcome == StageStatus.SKIP


def test_the_sql_handler_runs_as_the_target_and_reports_a_denial(monkeypatch):
    from databricks.sdk.service.sql import (
        ServiceError,
        StatementResponse,
        StatementState,
        StatementStatus,
    )

    from app.handlers.base import HandlerError
    from app.handlers.builtin import generic
    from app.handlers.builtin.sql_warehouse import SqlWarehouseHandler

    seen = {}
    resp = StatementResponse(status=StatementStatus(state=StatementState.FAILED, error=ServiceError(
        message="[INSUFFICIENT_PERMISSIONS] User does not have SELECT on Table.")))
    client = SimpleNamespace(statement_execution=SimpleNamespace(execute_statement=lambda **kw: resp))
    monkeypatch.setattr(generic, "_make_workspace_client", lambda host, token: seen.update(token=token) or client)

    def ambient():
        raise AssertionError("must not use the app's own credentials")

    with pytest.raises(HandlerError) as err:
        SqlWarehouseHandler().stage_operate(_live_ctx({"statement": "SELECT * FROM t"}, factory=ambient))
    assert seen == {"token": "tok"}
    assert err.value.failure_bucket == "PERMISSION_DENIED"


def test_a_quick_run_skips_a_check_that_judges_the_response(client):
    from helpers import target_id, test_id

    run_id = client.post("/api/v1/runs", json={"test_ids": [test_id(client, "SQL Warehouse validation")],
                                               "target_ids": [target_id(client, "read-only")]}).json()["run_id"]
    resource = client.get(f"/api/v1/runs/{run_id}").json()
    (cr,) = resource["run_ids"]
    detail = client.get(f"/api/v1/runs/{cr}").json()
    assert detail["verdict"] == "SKIPPED" and "full depth" in detail["message"]
    assert resource["functional"] is None


def test_a_safety_block_does_not_read_as_broken_connectivity(client):
    from helpers import target_id

    chk = client.post("/api/v1/checks", json={
        "display_name": "over-cap delete", "operation": {"service": "clusters", "method": "delete"},
        "assertion": "the operation succeeds"}).json()
    tst = client.post("/api/v1/tests", json={"name": "over-cap", "check_ids": [chk["id"]]}).json()
    run_id = client.post("/api/v1/runs", json={"test_ids": [tst["id"]], "target_ids": [target_id(client, "read-only")],
                                               "depth": "full"}).json()["run_id"]
    resource = client.get(f"/api/v1/runs/{run_id}").json()
    assert resource["verdict"] == "BLOCKED_BY_SAFETY"
    assert resource["connectable"] is None
