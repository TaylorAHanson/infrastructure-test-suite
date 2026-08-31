"""Primary seam: the Assistant HTTP endpoint (spec #22 Testing Decisions).

Drives the Assistant endpoint with a page context + a turn against a **stubbed
model-serving client** (injected via the ``get_model_call`` dependency override),
asserting Draft creation, validator rejection, blocklist/R3 refusal, a
run-as-role suggestion grounded on the target's registered roles, version
creation on edit, draft-never-runs, and advisory RCA — never the LLM's wording.
"""
from __future__ import annotations

import json

import pytest

from app.main import app
from app.services.assistant import get_model_call


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_model_call, None)


def set_model(plan):
    """Override the pinned-model dependency with a stub returning ``plan`` JSON.

    ``plan`` may be a dict (returned for every call) or a callable(messages)->dict.
    """
    def factory():
        def call(messages):
            p = plan(messages) if callable(plan) else plan
            return json.dumps(p)
        return call
    app.dependency_overrides[get_model_call] = factory


SQL_PLAN = {
    "name": "SQL warehouse returns rows",
    "operation": {"service": "statement_execution", "method": "execute_statement",
                  "verb": "GET", "params": {"statement": "SELECT 1", "demo_row_count": 1}},
    "assertion": "the query returns at least one row",
    "handler_key": "sql_warehouse",
}


# --------------------------- tool-calling plumbing (#57) ---------------------------
def test_tool_inventory_is_closed_by_omission():
    # The registered tool schemas are exactly the 8 read/grounding tools plus the
    # 4 suite-composition proposal tools (issue #63). No schema exists for a Target
    # write, /admin/*, Branding, or Webhook action — closed by omission at design
    # time (a missing schema, not a runtime refusal).
    from app.services.assistant_graph import TOOL_REGISTRY, TOOL_SCHEMAS

    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {
        "search_sdk_surface", "search_targets", "search_roles", "search_examples",
        "search_tests",
        "run_history", "commit_provenance_thread", "failure_archive", "operation_reference",
        # Suite composition tools (issue #63)
        "compose_suite_add_test", "compose_suite_remove_test",
        "compose_suite_reorder", "compose_suite_toggle_det_only",
    }
    assert set(TOOL_REGISTRY) == names
    for forbidden in ("register_target", "edit_target", "delete_target", "target_register",
                      "admin_seed", "purge_runs", "purge_all", "seed", "set_branding",
                      "branding", "configure_webhook", "webhook", "submit_run", "create_run",
                      "add_suite_member", "toggle_deterministic_only"):
        assert forbidden not in names


def test_turn_resolves_via_scripted_tool_call_sequence(client):
    # The turn is a loop: the model first calls a grounding tool, then answers.
    # The stub scripts that sequence (a tool_calls step, then the final plan).
    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"name": "search_sdk_surface",
                                    "arguments": {"query": "sql warehouse"}}]}
        return SQL_PLAN

    set_model(script)
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "run a SQL query and confirm it returns rows",
        "page_context": {"page": "test-studio"}})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "authoring"
    assert body["draft"]["checks"][0]["compilable"] is True
    # Tool-call transparency (additive): the executed tool is surfaced with a result.
    executed = body.get("tool_calls") or []
    names = [t["name"] for t in executed]
    assert "search_sdk_surface" in names
    assert calls["n"] >= 2  # looped: tool step, then the answering step


# --------------------------- authoring turn ---------------------------
def test_turn_authoring_returns_valid_draft(client):
    set_model(SQL_PLAN)
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "run a SQL query and confirm it returns rows",
        "page_context": {"page": "test-studio"}})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "authoring"
    draft = body["draft"]
    assert draft["clarifying_question"] is None
    assert len(draft["checks"]) == 1
    assert draft["checks"][0]["compilable"] is True
    assert draft["max_risk_tier"] == "R0"


def test_turn_asks_a_clarifying_question_when_slot_missing(client):
    set_model({})  # model yields nothing usable
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "zzzz qwerty", "page_context": {"page": "test-studio"}})
    assert r.status_code == 200
    assert r.json()["draft"]["clarifying_question"]


def test_turn_grounds_on_sdk_surface_operation_not_pre_declared(client):
    set_model({"operation": {"service": "jobs", "method": "list", "params": {}},
               "assertion": "the operation succeeds", "name": "list jobs"})
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "list the jobs", "page_context": {"page": "test-studio"}})
    draft = r.json()["draft"]
    assert draft["checks"][0]["operation"]["service"] == "jobs"


def test_blocklisted_operation_refused_in_turn_and_on_commit(client):
    set_model({"operation": {"service": "workspaces", "method": "delete", "params": {}},
               "assertion": "the workspace is deleted", "name": "delete ws"})
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "delete the workspace", "page_context": {"page": "test-studio"}})
    draft = r.json()["draft"]
    assert draft["checks"] == [] and draft["refusals"]

    # Committing a Draft that names the blocklisted op is refused by the validator.
    commit = client.post("/api/v1/assistant/draft", json={
        "name": "danger", "checks": [
            {"operation": {"service": "workspaces", "method": "delete"},
             "assertion": "it is deleted", "handler_key": "databricks_sdk"}]})
    assert commit.status_code == 422


# --------------------------- run-as-role grounding ---------------------------
def _make_sandbox_target_with_role(client) -> str:
    r = client.post("/api/v1/targets", json={
        "name": "sbx-roles", "workspace_name": "sbx-ws", "safety_class": "sandbox",
        "identity_handle": "sp-scope/analyst-key",
        "assumable_roles": [{"group_id": "g-analyst", "label": "analyst",
                             "safety_class": "read-only"}]})
    assert r.status_code in (200, 201)
    return r.json()["id"]


def test_run_as_role_only_suggested_when_target_registers_it(client):
    tid = _make_sandbox_target_with_role(client)
    base = {"operation": {"service": "clusters", "method": "create", "params": {}},
            "assertion": "the operation succeeds", "name": "c"}

    set_model({**base, "run_as_role": "analyst"})
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "start a cluster as the analyst",
        "page_context": {"page": "test-studio", "target_id": tid}})
    assert r.json()["draft"]["suggested_run_as_role"]["role"]["label"] == "analyst"

    set_model({**base, "run_as_role": "ghost"})  # invented role
    r2 = client.post("/api/v1/assistant/turn", json={
        "turn": "start a cluster as ghost",
        "page_context": {"page": "test-studio", "target_id": tid}})
    assert r2.json()["draft"]["suggested_run_as_role"] is None


# --------------------------- Draft → accept → runs ---------------------------
def _commit_sql_draft(client) -> str:
    r = client.post("/api/v1/assistant/draft", json={
        "name": "SQL rows", "description": "warehouse returns rows",
        "checks": [{"operation": SQL_PLAN["operation"], "assertion": SQL_PLAN["assertion"],
                    "handler_key": "sql_warehouse"}]})
    assert r.status_code == 201
    return r.json()["id"]


def test_confirmed_suggestions_are_applied_on_commit(client):
    # Suggest-then-human-confirms: when the human ticks the counterfactual +
    # run-as-role + suite, committing applies exactly those (user stories 6/7).
    suites = client.get("/api/v1/suites").json()
    suite_id = suites[0]["id"]
    r = client.post("/api/v1/assistant/draft", json={
        "name": "policy w/ counterfactual",
        "run_as_role": "analyst",
        "suite_id": suite_id,
        "checks": [
            {"operation": {"service": "clusters", "method": "create"},
             "assertion": "the operation succeeds", "handler_key": "databricks_sdk"},
            {"operation": {"service": "clusters", "method": "create",
                           "params": {"demo_outcome": "deny"}},
             "assertion": "a non-compliant cluster is denied", "kind": "counterfactual",
             "counterpart_index": 0, "handler_key": "databricks_sdk"}],
        "derivation": {"applied": {"run_as_role": {"role": "analyst", "why": "registered"}}}})
    assert r.status_code == 201
    body = r.json()
    assert body["run_as_role"] == "analyst"
    assert len(body["members"]) == 2
    cf = [m for m in body["members"] if m["kind"] == "counterfactual"][0]
    assert cf["counterpart_check_id"] is not None
    # Filed into the confirmed suite.
    suite = client.get(f"/api/v1/suites/{suite_id}").json()
    assert body["id"] in suite["test_ids"]


def test_committed_draft_is_a_draft(client):
    tid = _commit_sql_draft(client)
    body = client.get(f"/api/v1/tests/{tid}").json()
    assert body["status"] == "draft"
    assert body["origin"] == "assistant"


def _run_fanout(client, test_id: str) -> list[str]:
    """Submit a run for one Test and return its fan-out CheckRun ids.

    The Run API is async (ADR-0005): POST returns a run_id, the engine dispatches
    via a background task (which the TestClient drives to completion), and the
    fan-out CheckRuns appear on the verdict resource.
    """
    run_id = client.post("/api/v1/runs", json={
        "test_ids": [test_id], "requested_by": "test"}).json()["run_id"]
    return client.get(f"/api/v1/runs/{run_id}").json()["run_ids"]


def test_draft_never_runs_until_accepted(client):
    tid = _commit_sql_draft(client)
    # Running the draft produces no CheckRuns (a Draft never runs, user story 9).
    assert _run_fanout(client, tid) == []

    # Accept → Active, then it runs (at least one fan-out CheckRun).
    acc = client.post(f"/api/v1/tests/{tid}/accept")
    assert acc.status_code == 200 and acc.json()["status"] == "active"
    assert len(_run_fanout(client, tid)) >= 1


def test_accept_and_edit_create_new_versions(client):
    tid = _commit_sql_draft(client)  # v1 (draft)
    client.post(f"/api/v1/tests/{tid}/accept")  # v2 (active)
    client.patch(f"/api/v1/tests/{tid}", json={"description": "edited"})  # v3
    versions = client.get(f"/api/v1/tests/{tid}/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3]


# --------------------------- example promotion ---------------------------
def test_example_library_seeded_and_promotion_is_explicit(client):
    seeded = client.get("/api/v1/assistant/examples").json()
    # Gold authoring examples + curated onboarding starter prompts (ADR-0006 §9,
    # spec #56 stories 30/32); bumped from 4 when onboarding examples landed.
    assert len(seeded) >= 8

    tid = _commit_sql_draft(client)
    client.post(f"/api/v1/tests/{tid}/accept")
    promo = client.post("/api/v1/assistant/examples/promote", json={"test_id": tid})
    assert promo.status_code == 201
    after = client.get("/api/v1/assistant/examples").json()
    assert len(after) == len(seeded) + 1
    assert any(e.get("source_test_id") == tid for e in after)


def test_onboarding_starter_prompts_are_surfaced_via_search(client):
    """The shelf's starter prompts come from the WorkedExample library through
    the existing ``search_examples`` path — 3-4 curated onboarding intents
    tagged ``onboarding`` (spec #56 stories 30/32, ticket #60)."""
    starters = client.get("/api/v1/assistant/examples", params={"q": "onboarding"}).json()
    assert 3 <= len(starters) <= 4
    # Every surfaced starter is an onboarding-tagged example with clickable text.
    for ex in starters:
        assert "onboarding" in ex["tags"]
        assert ex["intent"].strip()  # the text a click populates the input with


# --------------------------- RCA + agentic (advisory) ---------------------------
def test_rca_endpoint_is_advisory_and_defaults_overnight(client):
    checks = client.get("/api/v1/checks").json()
    r = client.post("/api/v1/assistant/rca", json={"check_id": checks[0]["id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["advisory"] is True
    assert body["suggested_lane"] == "overnight"  # no failing history → default


def test_turn_routes_to_rca_when_a_finding_is_on_the_page(client):
    checks = client.get("/api/v1/checks").json()
    set_model({"cause": "unknown", "lane": "overnight", "confidence": "low"})
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "why did this start failing?",
        "page_context": {"page": "whats-interesting", "finding": {"check_id": checks[0]["id"]}}})
    assert r.json()["mode"] == "rca"
    assert r.json()["rca"]["advisory"] is True


def test_agentic_evaluate_endpoint_cites_field_and_is_advisory(client):
    set_model({"verdict": "pass", "cited_fields": ["row_count"], "rationale": "3 rows"})
    r = client.post("/api/v1/assistant/evaluate-agentic", json={
        "assertion": "the warehouse looks healthy-ish", "evidence": {"row_count": 3}})
    body = r.json()
    assert body["cited"] is True and body["cited_fields"] == ["row_count"]
    assert body["gating"] is False  # advisory: never gates CI (ADR-0008)


# --------------------------- search_tests (#62) ---------------------------
def test_search_tests_returns_seeded_checks(client):
    # search_tests returns existing Tests from the seeded catalog.
    from app.services.assistant_tools import search_tests
    from app.db.session import session_scope

    with session_scope() as s:
        results = search_tests(s, query="cluster")
    assert results, "Expected at least one seeded test matching 'cluster'"
    names = [r["name"] for r in results]
    assert any("cluster" in n.lower() for n in names)
    for r in results:
        assert "id" in r and "name" in r and "status" in r and "check_count" in r


def test_search_tests_filters_by_status(client):
    # status filter works: passing "active" returns only active tests.
    from app.services.assistant_tools import search_tests
    from app.db.session import session_scope

    with session_scope() as s:
        results = search_tests(s, query="", status="active")
    assert all(r["status"] == "active" for r in results)


def test_turn_query_for_existing_test_answers_directly(client):
    # A turn like "Do we have a test for cluster creation?" uses search_tests and
    # returns mode="query" with an answer — no draft, no clarifying question.
    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"name": "search_tests",
                                    "arguments": {"query": "cluster creation"}}]}
        return {"answer": "Yes, there is a test for cluster creation."}

    set_model(script)
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "Do we have a test for cluster creation?",
        "page_context": {"page": "test-studio"}})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "query"
    assert body["answer"]
    assert "draft" not in body
    executed = body.get("tool_calls") or []
    assert any(t["name"] == "search_tests" for t in executed)


# --------------------------- run_history global (#62) ---------------------------
def test_run_history_global_returns_most_recent_run_summary(client):
    # seed at least one run so run_history has something to summarise.
    checks = client.get("/api/v1/checks").json()
    run_id = client.post("/api/v1/runs", json={
        "test_ids": [], "check_ids": [checks[0]["id"]],
        "requested_by": "test"}).json()["run_id"]
    client.get(f"/api/v1/runs/{run_id}")  # drive background task

    from app.services.assistant_tools import run_history
    from app.db.session import session_scope

    with session_scope() as s:
        result = run_history(s)  # no IDs → global summary
    assert isinstance(result, dict), "Expected a dict summary when no IDs are given"
    assert "request_id" in result
    assert "total" in result
    for key in ("pass", "fail", "error", "blocked", "pending"):
        assert key in result


def test_turn_run_history_global_answers_without_draft(client):
    # "What happened last run?" uses run_history with no ids and answers via mode="query".
    # Create a run first so there is history to report.
    checks = client.get("/api/v1/checks").json()
    run_id = client.post("/api/v1/runs", json={
        "test_ids": [], "check_ids": [checks[0]["id"]],
        "requested_by": "test"}).json()["run_id"]
    client.get(f"/api/v1/runs/{run_id}")  # drive background task

    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"name": "run_history", "arguments": {}}]}
        return {"answer": "The last run had 1 check."}

    set_model(script)
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "What happened last run?",
        "page_context": {"page": "test-studio"}})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "query"
    assert body["answer"]
    executed = body.get("tool_calls") or []
    assert any(t["name"] == "run_history" for t in executed)


# --------------------- suite composition tools (issue #63) ---------------------

def test_suite_compose_add_test_returns_proposal(client):
    # Successful composition: add a Test to a non-deterministic-only Suite.
    # The tool returns a proposal (not applied) with requires_confirmation=True.
    suites = client.get("/api/v1/suites").json()
    suite = next(s for s in suites if not s["deterministic_only"])
    suite_id = suite["id"]
    all_tests = client.get("/api/v1/tests").json()
    available = [t["id"] for t in all_tests
                 if t["status"] == "active" and t["id"] not in suite["test_ids"]]
    if not available:
        pytest.skip("all active tests are already members of the non-det-only suite")
    test_id = available[0]

    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"name": "compose_suite_add_test",
                                    "arguments": {"suite_id": suite_id, "test_id": test_id}}]}
        return {"answer": f"Proposal ready — confirm to add {test_id} to {suite_id}."}

    set_model(script)
    r = client.post("/api/v1/assistant/turn", json={
        "turn": f"add test {test_id} to suite {suite_id}",
        "page_context": {"page": "suites", "suite_id": suite_id}})
    assert r.status_code == 200
    executed = r.json().get("tool_calls", [])
    result = next((t["result"] for t in executed if t["name"] == "compose_suite_add_test"), None)
    assert result is not None
    assert result["deterministic_violation"] is None
    assert result["requires_confirmation"] is True
    assert test_id in result["proposed_test_ids"]
    # Suite is unchanged until the human confirms — the tool only proposes.
    assert client.get(f"/api/v1/suites/{suite_id}").json()["test_ids"] == suite["test_ids"]


def test_suite_compose_det_only_violation_refused(client):
    # Adding a Test with an agentic Check to a deterministic_only Suite is refused
    # at the tool level and at the confirm endpoint.
    suites = client.get("/api/v1/suites").json()
    ci_gate = next(s for s in suites if s["deterministic_only"])
    suite_id = ci_gate["id"]

    # The seeded "Platform self-check" test contains warehouse_healthy_ish whose
    # assertion "the warehouse came up healthy-ish" compiles to agentic/non-compilable.
    all_tests = client.get("/api/v1/tests").json()
    agentic_test = next(
        (t for t in all_tests
         if ("platform" in t["name"].lower() or "self" in t["name"].lower())
         and t["id"] not in ci_gate["test_ids"]),
        None,
    )
    if agentic_test is None:
        pytest.skip("no agentic test available as non-member of the CI Gate suite")
    test_id = agentic_test["id"]

    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"name": "compose_suite_add_test",
                                    "arguments": {"suite_id": suite_id, "test_id": test_id}}]}
        return {"answer": "Refused — that test contains agentic Checks."}

    set_model(script)
    r = client.post("/api/v1/assistant/turn", json={
        "turn": f"add test {test_id} to the CI gate suite",
        "page_context": {"page": "suites", "suite_id": suite_id}})
    assert r.status_code == 200
    executed = r.json().get("tool_calls", [])
    result = next((t["result"] for t in executed if t["name"] == "compose_suite_add_test"), None)
    assert result is not None
    assert result["deterministic_violation"] is not None
    assert "agentic" in result["deterministic_violation"].lower()

    # The confirm endpoint also refuses the same change (safety gate re-runs).
    cr = client.post("/api/v1/assistant/suite-compose/confirm",
                     json={"suite_id": suite_id, "action": "add_test", "test_id": test_id})
    assert cr.status_code == 422


def test_suite_compose_job_scheduled_warning_surfaced(client):
    # A Suite included in a past NIGHTLY run gets job_scheduled=True in the tool result,
    # making the extra-visible confirmation visible to the caller.
    suites = client.get("/api/v1/suites").json()
    suite = suites[0]
    suite_id = suite["id"]

    # Record a NIGHTLY RunRequest that references this suite explicitly.
    nr = client.post("/api/v1/runs", json={
        "suite_ids": [suite_id], "depth": "quick",
        "requested_by": "nightly-dab", "request_source": "NIGHTLY"})
    assert nr.status_code in (200, 202)

    all_tests = client.get("/api/v1/tests").json()
    non_member = next(
        (t["id"] for t in all_tests
         if t["id"] not in suite["test_ids"] and t["status"] == "active"),
        None,
    )
    if non_member is None:
        pytest.skip("all active tests are already members of the first suite")

    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"name": "compose_suite_add_test",
                                    "arguments": {"suite_id": suite_id, "test_id": non_member}}]}
        return {"answer": "⚠ This suite is used by the nightly job — extra confirmation needed."}

    set_model(script)
    r = client.post("/api/v1/assistant/turn", json={
        "turn": f"add test {non_member} to suite {suite_id}",
        "page_context": {"page": "suites", "suite_id": suite_id}})
    assert r.status_code == 200
    executed = r.json().get("tool_calls", [])
    result = next((t["result"] for t in executed if t["name"] == "compose_suite_add_test"), None)
    assert result is not None
    assert result["job_scheduled"] is True
