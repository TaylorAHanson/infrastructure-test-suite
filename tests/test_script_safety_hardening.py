"""Hardening of the script runtime: how SQL and raw REST calls are classified, the
blocklist's exact matching and secret reads, duplicate checks and demo errors at
run time, timeouts in counterfactuals, the sandbox's guard, egress probes to
internal addresses, and the script text the platform writes."""
from __future__ import annotations

import glob
import os
import sys
from types import SimpleNamespace

import pytest

from app.core.enums import Depth, SafetyClass
from app.core.operations import Operation, is_blocklisted
from app.script_runtime import codegen
from app.script_runtime.analysis import analyze
from app.script_runtime.demo_workspace import DemoWorkspace
from app.script_runtime.runner import (RunContext, _internal_destination, _sql_operation, raw_api_operation,
                                       run_script, sql_verb)


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    import app.services.run_service as rs

    monkeypatch.setattr(rs, "_demo_mode", lambda: True)


def _target(safety_class=SafetyClass.READ_ONLY):
    return SimpleNamespace(id="t1", name="t", workspace_name="ws", host="https://x",
                           safety_class=safety_class, tags={}, assumable_roles=[], identity_handle=None)


def _run(script, *, safety_class=SafetyClass.READ_ONLY, demo_mode=True, **kw):
    ctx = RunContext(target=_target(safety_class), depth=Depth.FULL, demo_mode=demo_mode, **kw)
    return run_script(script, ctx)


# ---------------------------------------------------------------------------
# SQL classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("statement, verb", [
    ("SELECT 1", "GET"),
    ("  (select * from t)", "GET"),
    ("WITH x AS (SELECT 1) SELECT * FROM x", "GET"),
    ("SHOW GRANTS ON TABLE t", "GET"),
    ("DESCRIBE HISTORY t", "GET"),
    ("EXPLAIN DELETE FROM t", "GET"),
    ("USE CATALOG main", "GET"),
    ("SET spark.sql.ansi.enabled = true", "GET"),
    ("SELECT replace(name, 'a', 'b') FROM t", "GET"),
    ("SELECT * FROM t WHERE note = 'drop table x; delete'", "GET"),
    ("SELECT 1 -- then drop table t", "GET"),
    ("INSERT INTO t VALUES (1)", "POST"),
    ("UPDATE t SET x = 1", "POST"),
    ("CREATE TABLE t (x INT)", "POST"),
    ("GRANT SELECT ON TABLE t TO `analysts`", "POST"),
    ("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", "POST"),
    ("SELECT 1; DROP TABLE t", "DELETE"),
    ("DELETE FROM t", "DELETE"),
    ("TRUNCATE TABLE t", "DELETE"),
    ("VACUUM t", "DELETE"),
    ("INSERT OVERWRITE t SELECT 1", "DELETE"),
    ("INSERT INTO t REPLACE WHERE d = '2026-01-01' SELECT 1", "DELETE"),
    ("CREATE OR REPLACE TABLE t AS SELECT 1", "POST"),
    ("MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE", "DELETE"),
    ("ALTER TABLE t DROP COLUMN c", "DELETE"),
])
def test_sql_is_classified_by_what_it_does(statement, verb):
    assert sql_verb(statement) == verb


@pytest.mark.parametrize("statement", [
    "DROP CATALOG main",
    "select 1; drop schema main.s cascade",
    "EXECUTE IMMEDIATE 'DR' || 'OP CATALOG main'",
])
def test_catastrophic_and_dynamic_sql_are_blocklisted(statement):
    assert is_blocklisted(_sql_operation(statement))[0]


def test_a_drop_inside_a_string_is_only_a_read():
    op = _sql_operation("SELECT 'drop catalog main' AS note")
    assert not is_blocklisted(op)[0] and op.verb == "GET"


# ---------------------------------------------------------------------------
# Raw REST classification
# ---------------------------------------------------------------------------
def test_raw_rest_is_classified_like_the_sdk():
    assert raw_api_operation("POST", "/api/2.0/clusters/permanent-delete").verb == "DELETE"
    assert raw_api_operation("POST", "/api/2.0/clusters/create").verb == "POST"
    assert raw_api_operation("GET", "/api/2.0/clusters/list").verb == "GET"
    assert raw_api_operation("POST", "/api/2.0/sql/statements/", {"statement": "SELECT 1"}).verb == "GET"
    drop = raw_api_operation("POST", "/api/2.0/sql/statements", {"statement": "DROP CATALOG main", "warehouse_id": "w"})
    assert is_blocklisted(drop)[0]
    assert is_blocklisted(raw_api_operation("GET", "/api/2.0/secrets/get?scope=s&key=k"))[0]
    assert is_blocklisted(raw_api_operation("DELETE", "/api/2.1/unity-catalog/catalogs/main"))[0]


def test_analysis_judges_a_raw_statement_by_its_sql():
    out = analyze('with check("x"):\n'
                  '    w.api.post("/api/2.0/sql/statements", body={"statement": "DROP SCHEMA main.s", "warehouse_id": "w"})\n')
    assert not out["ok"] and "blocklist" in out["errors"][0]["message"]


def test_the_runtime_gate_judges_a_raw_statement_by_its_sql():
    res = _run('with check("x"):\n'
               '    w.api.post("/api/2.0/sql/statements", body={"statement": "DROP CATALOG main"})\n',
               safety_class=SafetyClass.SANDBOX)
    assert res.checks[0].outcome == "blocked"


# ---------------------------------------------------------------------------
# Blocklist: exact service names, and secret values
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("op", [
    Operation(service="workspace", method="delete"),  # a notebook or folder
    Operation(service="tables", method="delete"),
    raw_api_operation("POST", "/api/2.0/workspace/delete"),
    raw_api_operation("DELETE", "/api/2.1/unity-catalog/tables/main.default.t"),
    raw_api_operation("DELETE", "/api/2.1/unity-catalog/schemas/main.s"),
])
def test_ordinary_deletes_are_not_catastrophic(op):
    assert not is_blocklisted(op)[0]


@pytest.mark.parametrize("op", [
    Operation(service="workspaces", method="delete"),
    Operation(service="metastores", method="delete"),
    Operation(service="catalogs", method="delete"),
    Operation(service="storage_credentials", method="delete"),
    Operation(service="secrets", method="get_secret"),
])
def test_catastrophic_operations_stay_blocklisted(op):
    assert is_blocklisted(op)[0]


def test_notebook_cleanup_can_be_saved():
    out = analyze('with check("x"):\n    w.workspace.delete(path="/Users/x/vp-test-nb")\n    assert True\n')
    assert out["ok"] and out["calls"][0]["tier"] == "R2"


def test_neither_scripts_nor_the_assistant_can_read_a_secret_value(client):
    out = analyze('with check("x"):\n    w.secrets.get_secret(scope="s", key="k")\n')
    assert not out["ok"] and "secret" in out["errors"][0]["message"]

    from app.db.session import session_scope
    from app.services.editor_agent import EditorState, _t_sdk_call

    call = {"service": "secrets", "method": "get_secret", "kwargs": {"scope": "s", "key": "k"}}
    with session_scope() as session:
        st = EditorState(session=session, target=_target(SafetyClass.SANDBOX), buffer="", test=None)
        assert "secret" in str(_t_sdk_call(st, call).get("error", "")).lower()


def test_the_assistant_reads_nothing_live_without_a_target(client):
    from app.db.session import session_scope
    from app.services.editor_agent import EditorState, _t_run_sql, _t_sdk_call

    with session_scope() as session:
        st = EditorState(session=session, target=None, buffer="", test=None)
        assert "select a target" in _t_sdk_call(st, {"service": "clusters", "method": "list"})["error"]
        assert "select a target" in _t_run_sql(st, {"statement": "SELECT 1"})["error"]


def test_a_misspelled_field_raises_but_a_missing_one_reads_none():
    res = _run('with check("typo"):\n'
               '    me = w.current_user.me()\n'
               '    me.user_nmae\n'
               'with check("absent"):\n'
               '    me = w.current_user.me()\n'
               '    assert me.emails is None and me.get("user_nmae") is None and me.user_name\n')
    typo, absent = res.checks
    assert typo.outcome in ("fail", "error") and "did you mean 'user_name'" in typo.message
    assert absent.outcome == "pass", absent.message


# ---------------------------------------------------------------------------
# Check names
# ---------------------------------------------------------------------------
def test_a_check_name_built_at_run_time_cannot_be_saved():
    out = analyze('for wh in ["a", "b"]:\n    with check(f"{wh} auto-stops"):\n        assert wh\n')
    assert not out["ok"] and "string literal" in out["errors"][0]["message"]


def test_two_checks_in_one_with_are_refused():
    out = analyze('with check("a"), check("b"):\n    assert True\n')
    assert not out["ok"] and "One check per" in out["errors"][0]["message"]


def test_a_check_that_runs_twice_is_an_error_not_its_first_result():
    res = _run('for i in range(2):\n    with check("same"):\n        assert i == 0\n')
    (only,) = res.checks
    assert only.outcome == "error" and "more than once" in only.message


# ---------------------------------------------------------------------------
# Runtime: demo errors and counterfactual timeouts
# ---------------------------------------------------------------------------
def test_a_demo_handler_crash_is_the_scripts_error(monkeypatch):
    def boom(self, kind, msg):
        raise ValueError("bad run_id")

    monkeypatch.setattr(DemoWorkspace, "respond", boom)
    res = _run('with check("x"):\n    w.clusters.list()\n')
    assert res.error is None
    assert res.checks[0].outcome in ("fail", "error") and "bad run_id" in res.checks[0].message


def _network_reply(bucket):
    def respond(self, kind, msg):
        return None, {"kind": "connect", "stage": "CONNECT", "bucket": bucket, "message": bucket.lower()}
    return respond


def test_a_timeout_is_not_the_network_denial_expected(monkeypatch):
    monkeypatch.setattr(DemoWorkspace, "respond", _network_reply("UPSTREAM_TIMEOUT"))
    res = _run('with expect_deny("egress is blocked", at="CONNECT"):\n    http("https://example.com")\n')
    assert res.checks[0].outcome == "error" and "outage" in res.checks[0].message


def test_a_refused_connection_is_the_network_denial_expected(monkeypatch):
    monkeypatch.setattr(DemoWorkspace, "respond", _network_reply("NETWORK_POLICY_DENY"))
    res = _run('with expect_deny("egress is blocked", at="CONNECT"):\n    http("https://example.com")\n')
    assert res.checks[0].outcome == "pass"


# ---------------------------------------------------------------------------
# Egress probes can't reach the app's own host or the metadata service
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/api/v1/targets",
    "http://localhost/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://0.0.0.0/",
])
def test_http_refuses_internal_addresses(url):
    assert _internal_destination(url)


def test_http_allows_private_addresses():
    assert _internal_destination("http://10.0.0.5/") is None


def test_an_internal_http_probe_is_blocked_before_it_is_sent():
    res = _run('with check("x"):\n    http("http://127.0.0.1:9/")\n', demo_mode=False)
    assert res.checks[0].outcome == "blocked" and "loopback" in res.checks[0].message


# ---------------------------------------------------------------------------
# The sandbox's guard
# ---------------------------------------------------------------------------
_ESCAPES = '''
import os


def refused(fn):
    try:
        fn()
    except (PermissionError, ImportError) as exc:
        return str(exc)
    return None


def load_native():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.ExtensionFileLoader(params["so_name"], params["so"])
    spec = importlib.util.spec_from_file_location(params["so_name"], params["so"], loader=loader)
    importlib.util.module_from_spec(spec)


attempts = {
    "subprocess": lambda: __import__("subprocess").run(["/usr/bin/true"]),
    "os.system": lambda: os.system("true"),
    "signal the app": lambda: os.kill(os.getppid(), 0),
    "ctypes": lambda: __import__("ctypes"),
    "socket": lambda: __import__("socket").socket(),
    "dns": lambda: __import__("socket").getaddrinfo("example.com", 443),
    "read a host file": lambda: open("/etc/hosts").read(),
    "list the root": lambda: os.listdir("/"),
    "write outside home": lambda: open(os.path.join(os.path.dirname(os.getcwd()), "vp-escape.txt"), "w"),
    "third-party package": lambda: __import__("httpx"),
    "native code by path": load_native,
    "walk objects": lambda: __import__("gc").get_objects(),
}
for label, fn in attempts.items():
    with check(label):
        message = refused(fn)
        record("refusal", message)
        assert message, f"{label} was allowed"

with check("the standard library and scratch files still work"):
    import json, re, datetime, zoneinfo, tempfile, pathlib
    pathlib.Path("scratch.txt").write_text(json.dumps({"ok": True}))
    assert json.loads(pathlib.Path("scratch.txt").read_text()) == {"ok": True}
    with tempfile.NamedTemporaryFile() as f:
        f.write(b"x")
    assert zoneinfo.ZoneInfo("UTC") and re.match("a", "a") and datetime.date.today()
'''


def test_the_sandbox_refuses_processes_sockets_native_code_and_host_files():
    site = next(p for p in sys.path if p.endswith("site-packages"))
    native = sorted(glob.glob(os.path.join(site, "*.so")) + glob.glob(os.path.join(site, "*.pyd")))
    assert native, "need a third-party extension module to try loading"
    so_name = os.path.basename(native[0]).split(".")[0]
    res = _run(_ESCAPES, params={"so": native[0], "so_name": so_name})
    assert res.error is None, res.error
    outcomes = {c.name: (c.outcome, c.message) for c in res.checks}
    assert all(outcome == "pass" for outcome, _ in outcomes.values()), outcomes
    assert len(outcomes) == 13


# ---------------------------------------------------------------------------
# Script text the platform writes
# ---------------------------------------------------------------------------
def test_the_new_test_template_is_served_and_analyzes_clean(client):
    r = client.get("/api/v1/scripts/template", params={"name": "Warehouses auto-stop"})
    assert r.status_code == 200, r.text
    out = analyze(r.json()["script"])
    assert out["ok"], out["errors"]


def _legacy_check(**kw):
    base = dict(id="c1", display_name="analyst cannot delete", expectation="DENY", evaluator="agentic",
                assertion="Analysts are denied.\nEven on Fridays.", expected_stage=None,
                operation={"service": "clusters", "method": "permanent_delete", "params": {"cluster_id": "x"}},
                handler_key="databricks_sdk", compiled_predicate=None)
    return SimpleNamespace(**{**base, **kw})


def test_conversion_keeps_a_multiline_assertion_commented_and_advisory_counterfactuals():
    test = SimpleNamespace(name="Legacy", description="")
    member = SimpleNamespace(run_as_role=None, kind="counterfactual")
    script = codegen.structured_to_script(test, [(member, _legacy_check())])
    out = analyze(script)
    assert out["ok"], (out["errors"], script)
    assert "# Even on Fridays." in script
    assert out["checks"][0]["kind"] == "counterfactual" and out["checks"][0]["advisory"] is True

    plain = codegen.structured_to_script(test, [(SimpleNamespace(run_as_role=None, kind="assertion"),
                                                 _legacy_check(expectation="ALLOW", evaluator="deterministic"))])
    assert analyze(plain)["ok"], plain
