"""Python-script Tests: static analysis, the sandboxed runtime + gate, the API, the
run engine (one script run → one CheckRun per check) and the editor agent."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.enums import Depth, RequestSource, SafetyClass
from app.script_runtime.analysis import analyze
from app.script_runtime.runner import RunContext, run_script
from helpers import target_id as tid


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    import app.services.run_service as rs

    monkeypatch.setattr(rs, "_demo_mode", lambda: True)


def _target(safety_class=SafetyClass.READ_ONLY, roles=None):
    return SimpleNamespace(id="t1", name="t", workspace_name="ws", host="https://x",
                           safety_class=safety_class, tags={}, assumable_roles=roles or [],
                           identity_handle=None)


def _run(script, *, safety_class=SafetyClass.READ_ONLY, depth=Depth.FULL, roles=None, **kw):
    ctx = RunContext(target=_target(safety_class, roles), depth=depth, demo_mode=True, **kw)
    return run_script(script, ctx)


# ---------------------------------------------------------------------------
# Static analysis
# ---------------------------------------------------------------------------
def test_analysis_reports_syntax_errors_with_a_line():
    out = analyze("with check('x'):\n    w.clusters.list(\n")
    assert not out["ok"]
    assert out["errors"][0]["line"] and "Syntax error" in out["errors"][0]["message"]


def test_analysis_extracts_checks_calls_and_tiers():
    out = analyze(
        'with check("lists clusters"):\n'
        '    w.clusters.list()\n'
        'with expect_deny("analyst cannot delete", role="analyst"):\n'
        '    w.clusters.permanent_delete(cluster_id="x")\n')
    assert out["ok"]
    assert [(c["name"], c["kind"], c["role"]) for c in out["checks"]] == [
        ("lists clusters", "assertion", None), ("analyst cannot delete", "counterfactual", "analyst")]
    assert out["checks"][1]["expected_stage"] == "AUTHORIZE"
    tiers = {c["op"]: c["tier"] for c in out["calls"]}
    assert tiers == {"w.clusters.list": "R0", "w.clusters.permanent_delete": "R2"}
    assert out["max_risk_tier"] == "R2"


def test_analysis_suggests_a_fix_for_an_unknown_method():
    out = analyze('with check("x"):\n    w.clusters.lst()\n')
    assert not out["ok"]
    assert "did you mean list" in out["errors"][0]["message"]


def test_analysis_refuses_blocklisted_calls_and_catastrophic_sql():
    out = analyze('with check("x"):\n    w.metastores.delete(id="m")\n    sql("DROP CATALOG main")\n')
    assert not out["ok"]
    assert sum("blocklist" in e["message"] for e in out["errors"]) == 2


def test_analysis_refuses_duplicate_and_nested_checks():
    dup = analyze('with check("a"):\n    pass\nwith check("a"):\n    pass\n')
    assert not dup["ok"] and "Duplicate" in dup["errors"][0]["message"]
    nested = analyze('with check("a"):\n    with check("b"):\n        pass\n')
    assert not nested["ok"] and "nested" in nested["errors"][0]["message"]


def test_sql_reads_are_r0_and_writes_are_not():
    out = analyze('with check("x"):\n    sql("SELECT 1")\n    sql("INSERT INTO t VALUES (1)")\n')
    assert [c["tier"] for c in out["calls"]] == ["R0", "R1"]


# ---------------------------------------------------------------------------
# The sandboxed runtime and its gate
# ---------------------------------------------------------------------------
def test_checks_pass_fail_and_keep_going():
    res = _run('with check("ok"):\n    w.current_user.me()\n'
               'with check("bad"):\n    x = 1\n    assert x == 2\n'
               'with check("after"):\n    pass\n')
    by = {c.name: c for c in res.checks}
    assert by["ok"].outcome == "pass" and by["ok"].calls[0]["op"] == "current_user.me"
    assert by["bad"].outcome == "fail" and by["bad"].stage == "VERIFY"
    assert "assert x == 2" in by["bad"].message and by["bad"].line == 5
    assert by["after"].outcome == "pass"


def test_read_only_target_blocks_a_mutating_call_even_if_the_script_swallows_it():
    res = _run('with check("sneaky"):\n'
               '    try:\n        w.clusters.create(spark_version="x", cluster_name="y")\n'
               '    except Exception:\n        pass\n')
    check = res.check("sneaky")
    assert check.outcome == "blocked" and check.bucket == "BLOCKED_BY_SAFETY"
    assert check.calls[0]["outcome"] == "blocked"


def test_quick_depth_skips_mutating_calls_and_full_depth_runs_them_on_a_sandbox():
    script = 'with check("creates"):\n    w.clusters.create(spark_version="x", cluster_name="y")\n'
    assert _run(script, safety_class=SafetyClass.SANDBOX, depth=Depth.QUICK).check("creates").outcome == "skipped"
    assert _run(script, safety_class=SafetyClass.SANDBOX, depth=Depth.FULL).check("creates").outcome == "pass"


def test_blocklisted_calls_never_run_anywhere():
    res = _run('with check("x"):\n    w.metastores.delete(id="m")\n', safety_class=SafetyClass.SANDBOX)
    assert res.check("x").outcome == "blocked"


def test_expect_deny_passes_on_denial_and_fails_when_allowed():
    res = _run('with expect_deny("denied"):\n    raise PermissionDenied("no")\n'
               'with expect_deny("allowed"):\n    w.current_user.me()\n')
    assert res.check("denied").outcome == "pass"
    allowed = res.check("allowed")
    assert allowed.outcome == "fail" and allowed.bucket == "COUNTERFACTUAL_NOT_DENIED"


def test_unregistered_role_is_blocked():
    res = _run('with check("as analyst", role="analyst"):\n    w.current_user.me()\n')
    assert res.check("as analyst").outcome == "blocked"
    assert "not registered" in res.check("as analyst").message


def test_scripts_cannot_reach_credentials_or_sockets():
    res = _run('with check("config"):\n    w.config.token()\n'
               'with check("api_client"):\n    w.api_client.do("GET", "/api/2.0/clusters/list")\n'
               'with check("socket"):\n    import socket\n    socket.create_connection(("example.com", 443))\n'
               'with check("env"):\n    import os\n'
               '    assert not [k for k in os.environ if "DATABRICKS" in k or "PG" in k], list(os.environ)\n')
    assert res.check("config").outcome == "error"
    assert res.check("api_client").outcome == "error"
    assert res.check("socket").outcome == "error" and "network" in res.check("socket").message
    assert res.check("env").outcome == "pass", res.check("env").message


def test_a_crash_outside_checks_is_reported_with_its_line():
    res = _run('with check("first"):\n    pass\nundefined_name\nwith check("never"):\n    pass\n')
    assert res.check("first").outcome == "pass"
    assert res.check("never") is None
    assert res.error["kind"] == "error" and res.error["line"] == 3


def test_a_runaway_script_is_stopped_at_the_timeout():
    res = _run('with check("spin"):\n    while True:\n        pass\n', timeout_seconds=2)
    assert res.timed_out
    assert res.check("spin").outcome == "error"


# ---------------------------------------------------------------------------
# API + run engine
# ---------------------------------------------------------------------------
SCRIPT = '''"""Cluster policy guardrails"""
with check("can list clusters"):
    clusters = w.clusters.list()
    record("count", len(clusters))
    assert clusters

with check("creates a cluster"):
    w.clusters.create(spark_version="15.4.x-scala2.12", cluster_name="vp-test")

with expect_deny("random user is denied"):
    raise PermissionDenied("denied")
'''


def _create(client, script=SCRIPT, name="Guardrails"):
    r = client.post("/api/v1/tests/script", json={"name": name, "script": script})
    assert r.status_code == 201, r.text
    return r.json()


def test_creating_a_script_test_syncs_checks_as_members(client):
    test = _create(client)
    assert test["status"] == "draft" and test["script"] == SCRIPT
    kinds = [m["kind"] for m in test["members"]]
    assert kinds == ["assertion", "assertion", "counterfactual"]
    checks = {c["id"]: c for c in client.get("/api/v1/checks").json()}
    names = [checks[m["check_id"]]["display_name"] for m in test["members"]]
    assert names == ["can list clusters", "creates a cluster", "random user is denied"]
    assert checks[test["members"][1]["check_id"]]["risk_tier"] == "R1"


def test_saving_keeps_check_ids_for_unchanged_names(client):
    test = _create(client)
    before = [m["check_id"] for m in test["members"]]
    edited = SCRIPT.replace("record(\"count\", len(clusters))", "record(\"n\", len(clusters))") + \
        'with check("new one"):\n    pass\n'
    r = client.put(f"/api/v1/tests/{test['id']}/script", json={"script": edited})
    assert r.status_code == 200, r.text
    after = [m["check_id"] for m in r.json()["members"]]
    assert after[:3] == before and len(after) == 4
    assert r.json()["version"] == test["version"] + 1


def test_an_invalid_script_is_refused_with_its_analysis(client):
    test = _create(client)
    r = client.put(f"/api/v1/tests/{test['id']}/script",
                   json={"script": 'with check("x"):\n    w.workspace_conf.purge()\n'})
    assert r.status_code == 422
    assert r.json()["detail"]["analysis"]["ok"] is False


def _structured_tests(client):
    tests = [t for t in client.get("/api/v1/tests").json() if not t.get("script")]
    assert tests, "the tutorial Tests should still seed structured"
    return tests


def test_structured_tests_render_as_valid_scripts(client):
    for test in _structured_tests(client):
        out = client.get(f"/api/v1/tests/{test['id']}/script").json()
        assert out["converted"] is True
        assert out["analysis"]["ok"], (test["name"], out["analysis"]["errors"], out["script"])
        assert len(out["analysis"]["checks"]) == len(test["members"]), test["name"]


def test_converting_a_structured_test_by_saving_its_script(client):
    test = _structured_tests(client)[0]
    script = client.get(f"/api/v1/tests/{test['id']}/script").json()["script"]
    r = client.put(f"/api/v1/tests/{test['id']}/script", json={"script": script})
    assert r.status_code == 200, r.text
    assert r.json()["script"] == script
    assert client.get(f"/api/v1/tests/{test['id']}/script").json()["converted"] is False


def test_structured_edits_are_refused_on_a_script_test(client):
    test = _create(client)
    r = client.patch(f"/api/v1/tests/{test['id']}", json={"check_ids": []})
    assert r.status_code == 409
    assert client.patch(f"/api/v1/tests/{test['id']}", json={"name": "Renamed"}).status_code == 200


def test_rollback_restores_the_script_and_its_checks(client):
    test = _create(client)
    client.put(f"/api/v1/tests/{test['id']}/script", json={"script": 'with check("only"):\n    pass\n'})
    r = client.post(f"/api/v1/tests/{test['id']}/rollback/{test['version']}")
    assert r.status_code == 200, r.text
    assert r.json()["script"] == SCRIPT and len(r.json()["members"]) == 3


def _run_and_wait(client, test_id, target, depth="quick"):
    run = client.post("/api/v1/runs", json={"test_ids": [test_id], "target_ids": [target],
                                            "depth": depth, "requested_by": "pytest"}).json()
    return client.get(f"/api/v1/runs/{run['run_id']}").json()


def _check_runs(client, resource):
    runs = client.get("/api/v1/runs", params={"request_id": resource["run_id"]}).json()
    return {r["check_name"]: r for r in runs}


def test_a_run_executes_the_script_once_per_target_and_records_each_check(client):
    test = _create(client)
    client.post(f"/api/v1/tests/{test['id']}/accept")
    resource = _run_and_wait(client, test["id"], tid(client, "sandbox"), depth="quick")
    runs = _check_runs(client, resource)
    assert runs["can list clusters"]["verdict"] == "PASS"
    assert runs["can list clusters"]["evidence"]["count"] == 1
    assert runs["creates a cluster"]["verdict"] == "SKIPPED"
    assert runs["random user is denied"]["verdict"] == "PASS"
    assert resource["verdict"] == "PASS"

    full = _check_runs(client, _run_and_wait(client, test["id"], tid(client, "sandbox"), depth="full"))
    assert full["creates a cluster"]["verdict"] == "PASS"
    blocked = _check_runs(client, _run_and_wait(client, test["id"], tid(client, "read-only"), depth="full"))
    assert blocked["creates a cluster"]["verdict"] == "BLOCKED_BY_SAFETY"


def test_dry_run_runs_without_recording(client):
    before = len(client.get("/api/v1/runs/requests").json())
    r = client.post("/api/v1/scripts/dry-run", json={"script": SCRIPT, "target_id": tid(client, "read-only"),
                                                    "depth": "full"})
    assert r.status_code == 200, r.text
    outcomes = {c["name"]: c["outcome"] for c in r.json()["result"]["checks"]}
    assert outcomes == {"can list clusters": "pass", "creates a cluster": "blocked",
                        "random user is denied": "pass"}
    assert len(client.get("/api/v1/runs/requests").json()) == before


def test_sdk_reference_endpoints(client):
    hits = client.get("/api/v1/scripts/sdk/search", params={"q": "list clusters"}).json()
    assert any(h["service"] == "clusters" and h["method"] == "list" for h in hits)
    doc = client.get("/api/v1/scripts/sdk/clusters", params={"method": "create"}).json()
    assert doc["tier"] == "R1" and "spark_version" in doc["call"]
    assert client.get("/api/v1/scripts/sdk/nope").status_code == 404
    index = client.get("/api/v1/scripts/sdk/index").json()
    assert "list" in index["clusters"]


# ---------------------------------------------------------------------------
# Editor agent
# ---------------------------------------------------------------------------
def _scripted_model(steps):
    """A stub model that replays ``steps``: lists of tool calls, then a final string."""
    from app.clients.model_serving import ModelReply, ToolCall

    seen: list[list[dict]] = []

    def call(messages, tools):
        seen.append(messages)
        step = steps[min(len(seen) - 1, len(steps) - 1)]
        if isinstance(step, str):
            return ModelReply(content=step)
        return ModelReply(tool_calls=[ToolCall(name=n, arguments=a, id=f"c{i}") for i, (n, a) in enumerate(step)])

    call.seen = seen
    return call


def test_editor_agent_proposes_validates_and_dry_runs(client):
    from app.api.v1.assistant import get_editor_model
    from app.main import app
    proposal = 'with check("me"):\n    w.current_user.me()\n'
    model = _scripted_model([
        [("search_sdk", {"query": "current user"}), ("describe_sdk", {"service": "current_user", "method": "me"})],
        [("propose_script", {"script": proposal, "summary": "Checks the identity"})],
        [("dry_run", {})],
        "Added an identity check; it passes on the target.",
    ])
    app.dependency_overrides[get_editor_model] = lambda: model
    try:
        r = client.post("/api/v1/assistant/editor-turn", json={
            "messages": [{"role": "user", "content": "check the identity works"}],
            "script": "", "target_id": tid(client, "read-only")})
    finally:
        app.dependency_overrides.pop(get_editor_model, None)
    body = r.json()
    assert r.status_code == 200, body
    assert body["proposal"]["script"] == proposal and body["proposal"]["analysis"]["ok"]
    assert body["dry_run"]["result"]["checks"][0]["outcome"] == "pass"
    assert [s["tool"] for s in body["steps"]] == ["search_sdk", "describe_sdk", "propose_script", "dry_run"]
    assert body["reply"].startswith("Added")
    # Tool results were fed back to the model as tool messages after its tool calls.
    last = model.seen[-1]
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in last)
    assert any(m["role"] == "tool" for m in last)


def test_editor_agent_refuses_mutating_reads_and_off_site_docs(session):
    from app.services import editor_agent

    st = editor_agent.EditorState(session=session, target=None, buffer="", test=None)
    assert "error" in editor_agent._t_sdk_call(st, {"service": "clusters", "method": "delete",
                                                    "kwargs": {"cluster_id": "x"}})
    assert "error" in editor_agent._t_run_sql(st, {"statement": "DROP TABLE t"})
    assert "error" in editor_agent._t_read_docs(st, {"url": "https://evil.example.com/x"})


def test_editor_turn_without_a_model_explains_how_to_enable_it(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "MODEL_SERVING_ENDPOINT", None)
    r = client.post("/api/v1/assistant/editor-turn", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and r.json()["error"] is True


def test_calls_record_the_script_line_that_made_them():
    out = _run('x = 1\nwith check("a"):\n    w.clusters.list()\n')
    assert out.check("a").calls[0]["line"] == 3


def test_a_draft_that_never_ran_can_be_deleted_but_an_active_test_cannot(client):
    draft = _create(client, name="Throwaway")
    check_ids = {m["check_id"] for m in draft["members"]}
    assert client.delete(f"/api/v1/tests/{draft['id']}").status_code == 204
    assert client.get(f"/api/v1/tests/{draft['id']}").status_code == 404
    assert not check_ids & {c["id"] for c in client.get("/api/v1/checks").json()}

    kept = _create(client, name="Keeper")
    assert client.post(f"/api/v1/tests/{kept['id']}/accept").status_code == 200
    assert client.delete(f"/api/v1/tests/{kept['id']}").status_code == 409


def test_suites_are_created_edited_and_deleted_by_hand(client):
    a, b = _create(client, name="A"), _create(client, name="B")
    r = client.post("/api/v1/suites", json={"name": "Hand-made", "test_ids": [a["id"], b["id"]]})
    assert r.status_code == 201, r.text
    suite = r.json()
    r = client.patch(f"/api/v1/suites/{suite['id']}", json={"test_ids": [b["id"], a["id"]], "name": "Renamed"})
    assert r.status_code == 200 and r.json()["test_ids"] == [b["id"], a["id"]]
    assert client.delete(f"/api/v1/suites/{suite['id']}").status_code == 204
    assert suite["id"] not in {s["id"] for s in client.get("/api/v1/suites").json()}
    assert client.get(f"/api/v1/tests/{a['id']}").status_code == 200
    actions = {e["action"] for e in client.get("/api/v1/audit", params={"target": suite["id"]}).json()}
    assert {"suite.create", "suite.update", "suite.delete"} <= actions


def test_manual_suite_edits_keep_the_deterministic_only_invariant(client):
    from app.core.enums import Evaluator
    from app.db.session import session_scope
    from app.models import Check, Test, TestCheck

    with session_scope() as s:
        check = Check(display_name="judged", operation={"service": "jobs", "method": "list"},
                      handler_key="databricks_sdk", assertion="looks right", evaluator=Evaluator.AGENTIC,
                      compiled_predicate={})
        test = Test(name="agentic one", description="")
        s.add(check)
        s.add(test)
        s.flush()
        s.add(TestCheck(test_id=test.id, check_id=check.id, sort_order=0))
        s.commit()
        agentic_id = test.id
    r = client.post("/api/v1/suites", json={"name": "Strict", "test_ids": [agentic_id], "deterministic_only": True})
    assert r.status_code == 422 and "agentic" in r.text
    suite = client.post("/api/v1/suites", json={"name": "Loose", "test_ids": [agentic_id]}).json()
    r = client.patch(f"/api/v1/suites/{suite['id']}", json={"deterministic_only": True})
    assert r.status_code == 422
