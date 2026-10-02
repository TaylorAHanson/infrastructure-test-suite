"""Primary seam: the Assistant HTTP endpoint (spec #22 Testing Decisions).

Drives the Assistant endpoint with a page context + a turn against a **stubbed
model-serving client** (injected via the ``get_model_call`` dependency override),
asserting the closed tool inventory, catalog answers, the hand-off of a request
to write a Test to the test editor (never a draft), roles grounded on the
target's registry, and advisory RCA — never the LLM's wording.
"""
from __future__ import annotations

import json

import pytest

from app.main import app
from app.services.assistant import get_model_call

from helpers import script_test, target_id

READS = 'with check("lists jobs"):\n    w.jobs.list()\n'

#: The model's reply to a request to write a Test: the editor writes the script.
AUTHOR_PLAN = {"author": True, "answer": "Checks that a SQL query returns rows."}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_model_call, None)


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    from app.services import run_service

    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


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


def _turn(client, turn, **body):
    r = client.post("/api/v1/assistant/turn", json={
        "turn": turn, "page_context": {"page": "test-studio"}, **body})
    assert r.status_code == 200, r.text
    return r.json()


def _counts(client):
    return len(client.get("/api/v1/tests").json()), len(client.get("/api/v1/runs").json())


# --------------------------- tool-calling plumbing (#57) ---------------------------
def test_tool_inventory_is_closed_by_omission():
    # The registered tool schemas are the read/grounding tools plus search_suites (#64).
    # No schema exists for a Target write, /admin/*, Branding, Webhook, Suite write
    # or run submission — closed by omission at design time.
    from app.services.assistant_graph import TOOL_REGISTRY, TOOL_SCHEMAS

    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {
        "search_targets", "search_roles", "search_tests",
        "run_history", "commit_provenance_thread", "failure_archive",
        "search_suites",
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
            return {"tool_calls": [{"name": "search_tests", "arguments": {"query": "sql warehouse"}}]}
        return AUTHOR_PLAN

    set_model(script)
    body = _turn(client, "run a SQL query and confirm it returns rows")
    assert body["mode"] == "authoring" and "draft" not in body
    # Tool-call transparency (additive): the executed tool is surfaced with a result.
    assert "search_tests" in [t["name"] for t in body.get("tool_calls") or []]
    assert calls["n"] >= 2  # looped: tool step, then the answering step


def test_each_tool_result_answers_a_tool_call_in_the_turn_before_it(client):
    # Claude rejects a tool result whose id isn't a tool call in the previous message.
    seen: list[list[dict]] = []

    def script(messages):
        seen.append(messages)
        if len(seen) == 1:
            return {"tool_calls": [{"name": "search_tests", "arguments": {"query": "warehouse"}},
                                   {"id": "toolu_1", "name": "run_history", "arguments": {}}]}
        return AUTHOR_PLAN

    set_model(script)
    _turn(client, "write a test that a warehouse answers")
    turn, *results = seen[1][-3:]
    assert turn["role"] == "assistant" and [r["role"] for r in results] == ["tool", "tool"]
    ids = [c["id"] for c in turn["tool_calls"]]
    assert ids[1] == "toolu_1" and all(ids)
    assert [r["tool_call_id"] for r in results] == ids
    assert [c["function"]["name"] for c in turn["tool_calls"]] == ["search_tests", "run_history"]


# --------------------------- tool-call transparency + next actions (#65) --------
def test_turn_next_actions_are_registry_keyed():
    # The dynamic button set is driven generically by the tool registry — every
    # next-action builder is keyed by a real tool name, never a per-capability
    # hardcoded set. A builder for a non-tool would be dead weight.
    from app.services.assistant_graph import NEXT_ACTION_BUILDERS, TOOL_REGISTRY

    assert set(NEXT_ACTION_BUILDERS) <= set(TOOL_REGISTRY)


def test_turn_surfaces_navigate_next_action_from_run_history(client):
    # A turn that calls run_history surfaces a "view run history" navigate action,
    # derived from the tool that ran — additive alongside mode/answer/tool_calls.
    def script(messages):
        return {"tool_calls": [{"name": "run_history", "arguments": {}}]} \
            if messages[-1]["role"] != "tool" else AUTHOR_PLAN

    set_model(script)
    body = _turn(client, "what happened last run?")
    assert body["mode"] == "authoring" and "draft" not in body
    assert "run_history" in [t["name"] for t in body["tool_calls"]]
    assert any(a["kind"] == "navigate" and a["href"] == "/runs" for a in body["next_actions"])


def test_empty_search_offers_author_next_action(client):
    # When a grounding search comes back empty, the shelf offers "Author this
    # test" (spec #56 story 7) — generic, from the search tool's empty result.
    def script(messages):
        return {"tool_calls": [{"name": "search_tests",
                                "arguments": {"query": "zzzz nonexistent qqqq"}}]} \
            if messages[-1]["role"] != "tool" else AUTHOR_PLAN

    set_model(script)
    body = _turn(client, "do we have a test for zzzz nonexistent qqqq?")
    author = [a for a in body["next_actions"] if a["kind"] == "author"]
    assert author and author[0]["seed"].startswith("Create a new test that")


def test_next_actions_dedupe_across_repeated_tool_calls():
    from app.services.assistant_graph import build_next_actions

    executed = [
        {"name": "run_history", "arguments": {}, "result": []},
        {"name": "run_history", "arguments": {"check_id": "c1"}, "result": []},
    ]
    actions = build_next_actions(executed)
    assert len([a for a in actions if a["href"] == "/runs"]) == 1


# ------------------- a request to write a Test goes to the editor -------------------
def test_a_request_to_write_a_test_hands_off_to_the_editor_without_a_draft(client):
    set_model(AUTHOR_PLAN)
    before = _counts(client)
    body = _turn(client, "run a SQL query and confirm it returns rows")
    assert body["mode"] == "authoring"
    assert body["answer"] == AUTHOR_PLAN["answer"]
    assert "draft" not in body and "clarifying_question" not in body
    # Nothing is created or run: the editor (/tests/new?ask=) writes the script.
    assert _counts(client) == before


def test_a_model_that_gives_nothing_still_hands_off_to_the_editor(client):
    set_model({})  # model yields nothing usable
    body = _turn(client, "check every warehouse auto-stops within an hour")
    assert body["mode"] == "authoring" and body["answer"] is None
    assert "draft" not in body


def test_turn_conversational_greeting_gets_query_answer_not_a_hand_off(client):
    # A greeting/meta turn is not a request to write a Test at all — it must land
    # on mode="query" with a plain capability blurb, never open the editor on
    # "hi" (issue #112). The model gives nothing usable: this is the deterministic
    # guard's job (`assistant_graph.is_conversational_turn`), the model-free
    # safety net underneath the live-model system-prompt nudge.
    set_model({})
    body = _turn(client, "hi there, how are you doing today?")
    assert body["mode"] == "query"
    assert body["answer"]
    assert "draft" not in body


def test_junk_that_fuzzy_matches_a_greeting_word_is_not_answered_as_one(client):
    # Gibberish with a greeting-ish substring ("are") isn't conversational, so
    # it doesn't get the capability blurb — it goes to the editor like any ask.
    set_model({})
    body = _turn(client, "zzzz are blah blah nonsense")
    assert body["mode"] == "authoring"
    assert "draft" not in body


def test_a_run_submission_mode_never_proposes_or_submits_a_run(client):
    # The run-submission mode is gone: an old client asking for it gets an
    # ordinary turn, and no run is proposed or created.
    set_model({"run_proposal": {"target_ids": [target_id(client, "read-only")], "suite_ids": [],
                                "test_ids": [], "depth": "quick", "summary": "Run everything"}})
    before = _counts(client)
    body = _turn(client, "run the default suite on the read-only target", mode="run_submission")
    assert body["mode"] in ("authoring", "query")
    assert "run_proposal" not in body and "run_id" not in body
    assert _counts(client) == before


def test_the_draft_example_agentic_and_run_routes_are_gone(client):
    tid = script_test(client, READS, "Promotable")["id"]
    gone = [
        client.post("/api/v1/assistant/draft", json={"name": "x", "checks": []}),
        client.get("/api/v1/assistant/examples"),
        client.post("/api/v1/assistant/examples/promote", json={"test_id": tid}),
        client.post("/api/v1/assistant/evaluate-agentic", json={"assertion": "ok", "evidence": {}}),
        client.post("/api/v1/assistant/run", json={"test_ids": [tid], "target_ids": []}),
    ]
    assert all(r.status_code in (404, 405) for r in gone), [(r.request.url.path, r.status_code) for r in gone]


# --------------------------- run-as-role grounding ---------------------------
def test_roles_come_only_from_the_targets_registry(client):
    from app.db.session import session_scope
    from app.services.assistant_graph import dispatch_tool

    r = client.post("/api/v1/targets", json={
        "name": "sbx-roles", "workspace_name": "sbx-ws", "safety_class": "sandbox",
        "identity_handle": "sp-scope/analyst-key",
        "assumable_roles": [{"group_id": "g-analyst", "label": "analyst", "safety_class": "read-only"}]})
    assert r.status_code in (200, 201)
    with session_scope() as s:
        roles = dispatch_tool(s, None, "search_roles", {"target_ref": r.json()["id"]})
        assert [role["label"] for role in roles] == ["analyst"]
        assert dispatch_tool(s, None, "search_roles", {"target_ref": "no-such-target"}) == []


# --------------------------- Draft → accept → runs ---------------------------
def test_draft_never_runs_until_accepted(client):
    tid = script_test(client, READS, "SQL rows", active=False)["id"]
    # A Draft never runs (user story 9): a selection of only Drafts is refused.
    r = client.post("/api/v1/runs", json={"test_ids": [tid], "depth": "full", "request_source": "UI_MANUAL"})
    assert r.status_code == 400 and "Draft" in r.text

    # Accept → Active, then it runs (at least one fan-out CheckRun).
    acc = client.post(f"/api/v1/tests/{tid}/accept")
    assert acc.status_code == 200 and acc.json()["status"] == "active"
    run_id = client.post("/api/v1/runs", json={"test_ids": [tid], "requested_by": "test"}).json()["run_id"]
    assert len(client.get(f"/api/v1/runs/{run_id}").json()["run_ids"]) >= 1


def test_accept_and_edit_create_new_versions(client):
    tid = script_test(client, READS, "SQL rows", active=False)["id"]  # v1 (draft)
    client.post(f"/api/v1/tests/{tid}/accept")  # v2 (active)
    client.patch(f"/api/v1/tests/{tid}", json={"description": "edited"})  # v3
    versions = client.get(f"/api/v1/tests/{tid}/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3]


# --------------------------- RCA (advisory) ---------------------------
def _a_check_id(client):
    return script_test(client, READS, "RCA subject")["members"][0]["check_id"]


def test_rca_endpoint_is_advisory_and_defaults_overnight(client):
    r = client.post("/api/v1/assistant/rca", json={"check_id": _a_check_id(client)})
    assert r.status_code == 200
    body = r.json()
    assert body["advisory"] is True
    assert body["suggested_lane"] == "overnight"  # no failing history → default


def test_turn_routes_to_rca_when_a_finding_is_on_the_page(client):
    check_id = _a_check_id(client)
    set_model({"cause": "unknown", "lane": "overnight", "confidence": "low"})
    r = client.post("/api/v1/assistant/turn", json={
        "turn": "why did this start failing?",
        "page_context": {"page": "whats-interesting", "finding": {"check_id": check_id}}})
    assert r.json()["mode"] == "rca"
    assert r.json()["rca"]["advisory"] is True


# --------------------------- search_tests (#62) ---------------------------
def test_search_tests_returns_matching_tests(client):
    from app.services.assistant_tools import search_tests
    from app.db.session import session_scope

    script_test(client, READS, "Cluster tags audit")
    with session_scope() as s:
        results = search_tests(s, query="cluster")
    names = [r["name"] for r in results]
    assert "Cluster tags audit" in names
    for r in results:
        assert "id" in r and "name" in r and "status" in r and "check_count" in r


def test_search_tests_matches_script_text_below_name(client):
    # Script text is searchable, but a hit there ranks below a name/description hit.
    from app.db.session import session_scope
    from app.models import Test
    from app.services.assistant_tools import search_tests

    with session_scope() as s:
        s.add(Test(name="Zorblax grants audit", description="checks zorblax grants"))
        s.add(Test(name="Catalog sweep", description="walks catalogs",
                   script='with check("sweep"):\n    w.grants.get(full_name="zorblax")\n'))
        s.commit()
        results = search_tests(s, query="zorblax")

    names = [r["name"] for r in results]
    assert names[:2] == ["Zorblax grants audit", "Catalog sweep"]


def test_search_tests_filters_by_status(client):
    # status filter works: passing "active" returns only active tests.
    from app.services.assistant_tools import search_tests
    from app.db.session import session_scope

    script_test(client, READS, "Zorblax active")
    script_test(client, READS, "Zorblax draft", active=False)
    with session_scope() as s:
        results = search_tests(s, query="zorblax", status="active")
    assert [r["name"] for r in results] == ["Zorblax active"]


def test_turn_query_for_existing_test_answers_directly(client):
    # A turn like "Do we have a test for cluster creation?" uses search_tests and
    # returns mode="query" with an answer — no hand-off, no clarifying question.
    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"name": "search_tests",
                                    "arguments": {"query": "cluster creation"}}]}
        return {"answer": "Yes, there is a test for cluster creation."}

    set_model(script)
    body = _turn(client, "Do we have a test for cluster creation?")
    assert body["mode"] == "query"
    assert body["answer"]
    assert "draft" not in body
    assert any(t["name"] == "search_tests" for t in body.get("tool_calls") or [])


# --------------------------- run_history global (#62) ---------------------------
def _a_run(client):
    tid = script_test(client, READS, "History subject")["id"]
    run_id = client.post("/api/v1/runs", json={
        "test_ids": [tid], "target_ids": [target_id(client, "read-only")],
        "requested_by": "test"}).json()["run_id"]
    client.get(f"/api/v1/runs/{run_id}")
    return run_id


def test_run_history_global_returns_most_recent_run_summary(client):
    _a_run(client)

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
    _a_run(client)
    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tool_calls": [{"name": "run_history", "arguments": {}}]}
        return {"answer": "The last run had 1 check."}

    set_model(script)
    body = _turn(client, "What happened last run?")
    assert body["mode"] == "query"
    assert body["answer"]
    assert any(t["name"] == "run_history" for t in body.get("tool_calls") or [])


# ------------- suites are composed by hand, not by the Assistant -------------

def test_assistant_has_no_suite_writing_tools():
    from app.services.assistant_graph import TOOL_REGISTRY

    assert not [n for n in TOOL_REGISTRY if "suite" in n and n != "search_suites"]


def test_suite_compose_confirm_endpoint_is_gone(client):
    r = client.post("/api/v1/assistant/suite-compose/confirm", json={"action": "create", "name": "x"})
    assert r.status_code in (404, 405)
