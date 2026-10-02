"""The editor assistant's targeted edits, streamed progress, and admin settings."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.clients.model_serving import ModelReply, ToolCall
from helpers import target_id as tid

SCRIPT = '''with check("the identity resolves"):
    me = w.current_user.me()
    assert me.user_name

with check("clusters list"):
    w.clusters.list()
'''


def _scripted(steps):
    """A stub model replaying ``steps``: lists of (tool, args) calls, then a final string."""
    seen: list[list[dict]] = []

    def call(messages, tools):
        seen.append([dict(m) for m in messages])
        call.tools.append([t["function"]["name"] for t in tools])
        step = steps[min(len(seen) - 1, len(steps) - 1)]
        if isinstance(step, str):
            return ModelReply(content=step)
        text, calls = step if isinstance(step, tuple) else (None, step)
        return ModelReply(content=text, tool_calls=[ToolCall(name=n, arguments=a, id=f"c{len(seen)}-{i}")
                                                    for i, (n, a) in enumerate(calls)])

    call.seen = seen
    call.tools = []
    return call


def _state(session, buffer=SCRIPT):
    from app.services.editor_agent import EditorState

    return EditorState(session=session, target=None, buffer=buffer, test=None)


# ---------------------------------------------------------------------------
# edit_script
# ---------------------------------------------------------------------------
def test_edit_script_replaces_only_the_lines_it_names(session):
    from app.services.editor_agent import _t_edit

    st = _state(session)
    out = _t_edit(st, {"summary": "assert on the name", "edits": [
        {"old": "    assert me.user_name\n", "new": '    assert me.user_name, "no user name"\n    record("user", me.user_name)\n'}]})
    assert out["recorded"] and out["ok"], out
    assert st.proposal["script"] == SCRIPT.replace(
        "    assert me.user_name\n", '    assert me.user_name, "no user name"\n    record("user", me.user_name)\n')
    assert "record(\"user\"" in out["edited"] and "|" in out["edited"]


def test_edit_script_builds_on_its_previous_edit(session):
    from app.services.editor_agent import _t_edit

    st = _state(session)
    _t_edit(st, {"summary": "a", "edits": [{"old": "clusters list", "new": "clusters can be listed"}]})
    _t_edit(st, {"summary": "b", "edits": [{"old": "    w.clusters.list()", "new": "    assert w.clusters.list() is not None"}]})
    script = st.proposal["script"]
    assert 'check("clusters can be listed")' in script and "assert w.clusters.list() is not None" in script
    assert st.buffer == SCRIPT


@pytest.mark.parametrize("old, why", [("not in the script", "not found"), ("with check(", "matches 2 places")])
def test_edit_script_refuses_ambiguous_or_missing_text_and_applies_nothing(session, old, why):
    from app.services.editor_agent import _t_edit

    st = _state(session)
    out = _t_edit(st, {"summary": "x", "edits": [{"old": "clusters list", "new": "renamed"}, {"old": old, "new": "y"}]})
    assert why in out["error"] and "No edits were applied" in out["error"]
    assert st.proposal is None


def test_edit_script_accepts_edits_sent_as_a_json_string(session):
    from app.services.editor_agent import _t_edit

    st = _state(session)
    out = _t_edit(st, {"summary": "x", "edits": json.dumps([{"old": "clusters list", "new": "clusters listed"}])})
    assert out["ok"] and "clusters listed" in st.proposal["script"]


def test_view_script_numbers_the_working_copy(session):
    from app.services.editor_agent import _t_edit, _t_view

    st = _state(session)
    _t_edit(st, {"summary": "x", "edits": [{"old": "clusters list", "new": "clusters listed"}]})
    out = _t_view(st, {"start_line": 5, "end_line": 5})
    assert out["of"] == "your latest edit" and out["text"] == '   5| with check("clusters listed"):'


# ---------------------------------------------------------------------------
# The turn loop and streaming
# ---------------------------------------------------------------------------
def _sse(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_the_stream_reports_each_step_as_it_happens_then_the_same_result(client):
    from app.api.v1.assistant import get_editor_model
    from app.main import app

    model = _scripted([
        ("Let me find the method first.", [("search_sdk", {"query": "current user"})]),
        [("edit_script", {"summary": "Rename", "edits": [{"old": "clusters list", "new": "clusters can be listed"}]})],
        "Renamed the second check.",
    ])
    app.dependency_overrides[get_editor_model] = lambda: model
    try:
        r = client.post("/api/v1/assistant/editor-turn/stream", json={
            "messages": [{"role": "user", "content": "rename the cluster check"}], "script": SCRIPT})
    finally:
        app.dependency_overrides.pop(get_editor_model, None)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _sse(r.text)
    kinds = [e["type"] for e in events]
    assert kinds == ["thinking", "message", "tool_start", "tool_end", "thinking", "tool_start", "proposal",
                     "tool_end", "thinking", "done"]
    assert events[1]["text"] == "Let me find the method first."
    assert events[2]["detail"] == "Searching the SDK for “current user”"
    assert events[7]["ok"] and events[7]["result"].startswith("valid")
    done = events[-1]
    assert done["reply"] == "Renamed the second check."
    assert 'check("clusters can be listed")' in done["proposal"]["script"]
    assert [s["tool"] for s in done["steps"]] == ["search_sdk", "edit_script"]
    assert all("ms" in s for s in done["steps"])


def test_a_turn_stops_at_the_tool_call_budget_and_wraps_up(session):
    from app.services import assistant_config, editor_agent

    assistant_config.update_config(session, actor="t", max_tool_calls=2)
    model = _scripted([[("search_sdk", {"query": "a"}), ("search_sdk", {"query": "b"}), ("search_sdk", {"query": "c"})],
                       "Here's where I got to."])
    events: list[dict] = []
    out = editor_agent.editor_turn(session, messages=[{"role": "user", "content": "go"}], buffer=SCRIPT,
                                   model_call=model, emit=events.append)
    assert [s["tool"] for s in out["steps"]] == ["search_sdk", "search_sdk"]
    assert out["reply"] == "Here's where I got to."
    assert any(e["type"] == "notice" and "limit of 2 tool calls" in e["text"] for e in events)
    last = model.seen[-1]
    assert last[-1]["role"] == "user" and "budget" in last[-1]["content"]
    skipped = [m for m in last if m["role"] == "tool" and "not run" in m["content"]]
    assert len(skipped) == 1


def test_a_cancelled_turn_stops_before_the_next_step(session):
    import threading

    from app.services import editor_agent

    cancel = threading.Event()

    def model(messages, tools):
        cancel.set()
        return ModelReply(tool_calls=[ToolCall(name="search_sdk", arguments={"query": "x"}, id="c")])

    out = editor_agent.editor_turn(session, messages=[{"role": "user", "content": "go"}], buffer="",
                                   model_call=model, cancel=cancel)
    assert out["stopped"] and out["steps"] == []


def test_turning_off_live_reads_and_dry_runs_removes_those_tools(session):
    from app.services import assistant_config, editor_agent

    assistant_config.update_config(session, actor="t", allow_live_reads=False, allow_dry_run=False)
    model = _scripted([[("dry_run", {})], "ok"])
    out = editor_agent.editor_turn(session, messages=[{"role": "user", "content": "go"}], buffer=SCRIPT,
                                   model_call=model)
    offered = model.tools[0]
    assert not {"sdk_call", "run_sql", "dry_run"} & set(offered) and "edit_script" in offered
    assert out["steps"][0]["ok"] is False and out["dry_run"] is None
    assert "Turned off by the admin" in model.seen[0][0]["content"]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def test_settings_default_to_the_deployment_endpoint_and_changes_are_audited(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "MODEL_SERVING_ENDPOINT", "databricks-claude-sonnet-4-5")
    cfg = client.get("/api/v1/assistant/config").json()
    assert cfg["endpoint"] is None and cfg["model"] == "databricks-claude-sonnet-4-5"
    assert cfg["limits"]["max_tool_calls"] == [1, 200]

    r = client.put("/api/v1/assistant/config", json={"endpoint": "databricks-gpt-5-5", "reasoning": "medium",
                                                     "max_tool_calls": 12})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "databricks-gpt-5-5" and body["reasoning"] == "medium" and body["max_tool_calls"] == 12

    from app.clients.model_serving import default_endpoint

    assert default_endpoint() == "databricks-gpt-5-5"
    audit = client.get("/api/v1/audit", params={"action": "assistant.config.change"}).json()
    items = audit["items"] if isinstance(audit, dict) else audit
    assert any("endpoint" in (e.get("summary") or "") for e in items)

    assert client.put("/api/v1/assistant/config", json={"endpoint": ""}).json()["model"] == "databricks-claude-sonnet-4-5"


@pytest.mark.parametrize("payload", [
    {"endpoint": "../../api/2.0/secrets"}, {"reasoning": "extreme"}, {"max_tool_calls": 0},
    {"temperature": 3}, {"turn_timeout_seconds": 5},
])
def test_settings_reject_values_outside_their_range(client, payload):
    r = client.put("/api/v1/assistant/config", json=payload)
    assert r.status_code == 422, r.text


def test_the_model_list_offers_chat_endpoints_with_foundation_models_first(monkeypatch):
    from app.core import config
    from app.services import assistant_config

    def ep(name, task, entity, ready="READY"):
        return SimpleNamespace(name=name, task=task, state=SimpleNamespace(ready=SimpleNamespace(value=ready)),
                               config=SimpleNamespace(served_entities=[SimpleNamespace(entity_name=entity)]))

    endpoints = [ep("my-agent", "agent/v1/responses", "main.a.agent"), ep("zeta-custom", "llm/v1/chat", "main.m.x"),
                 ep("databricks-bge-large-en", "llm/v1/embeddings", "system.ai.bge"),
                 ep("databricks-gpt-5", "llm/v1/chat", "system.ai.databricks-gpt-5"),
                 ep("databricks-claude-opus-4-1", "llm/v1/chat", "system.ai.databricks-claude-opus-4-1", "NOT_READY")]
    fake = SimpleNamespace(serving_endpoints=SimpleNamespace(list=lambda: endpoints))
    monkeypatch.setattr(config, "get_workspace_client", lambda: fake)
    monkeypatch.setattr("app.services.run_service._demo_mode", lambda: False)
    out = assistant_config.list_models(refresh=True)
    assert [m["name"] for m in out["models"]] == ["databricks-gpt-5", "databricks-claude-opus-4-1", "zeta-custom"]
    assert out["models"][0] == {"name": "databricks-gpt-5", "entity": "system.ai.databricks-gpt-5",
                                "ready": True, "foundation": True}


# ---------------------------------------------------------------------------
# Model client
# ---------------------------------------------------------------------------
def _fake_client(monkeypatch, replies):
    from app.clients import model_serving

    bodies: list[dict] = []

    def do(method, path, body=None, headers=None):
        bodies.append(body)
        reply = replies[min(len(bodies) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(model_serving, "get_workspace_client",
                        lambda: SimpleNamespace(api_client=SimpleNamespace(do=do)))
    monkeypatch.setattr(model_serving, "_learned", {})
    return bodies


def test_claude_gets_a_thinking_budget_and_its_reasoning_comes_back(monkeypatch):
    from app.clients.model_serving import chat_with_tools

    bodies = _fake_client(monkeypatch, [{"choices": [{"message": {"content": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "17*23 is 391", "signature": "s"}]},
        {"type": "text", "text": "391"}]}}]}])
    reply = chat_with_tools([{"role": "user", "content": "hi"}], endpoint="databricks-claude-sonnet-4-5",
                            max_tokens=2000, temperature=0.1, reasoning="low")
    assert reply.content == "391" and reply.reasoning == "17*23 is 391"
    assert bodies[0]["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert bodies[0]["max_tokens"] == 3024 and "temperature" not in bodies[0]


def test_a_model_that_refuses_a_reasoning_level_is_retried_without_one(monkeypatch):
    from app.clients.model_serving import chat_with_tools

    bodies = _fake_client(monkeypatch, [
        RuntimeError("Function tools with reasoning_effort are not supported for gpt-5.5"),
        {"choices": [{"message": {"content": "ok"}}]}])
    reply = chat_with_tools([{"role": "user", "content": "hi"}], tools=[{"type": "function"}],
                            endpoint="databricks-gpt-5-5", reasoning="high")
    assert bodies[0]["reasoning_effort"] == "high" and "reasoning_effort" not in bodies[1]
    assert reply.content == "ok" and "without one" in reply.notes[0]


OPUS_ADAPTIVE = RuntimeError('{"message":"\\"thinking.type.enabled\\" is not supported for this model. '
                             'Use \\"thinking.type.adaptive\\" and \\"output_config.effort\\" to control thinking."}')
NO_TEMPERATURE = RuntimeError("BAD_REQUEST: Model us.anthropic.claude-opus-5-5 does not support the temperature parameter.")
OK = {"choices": [{"message": {"content": "ok"}}]}


def test_newer_claude_switches_to_adaptive_thinking_and_remembers(monkeypatch):
    from app.clients.model_serving import chat_with_tools

    bodies = _fake_client(monkeypatch, [OPUS_ADAPTIVE, OK])
    reply = chat_with_tools([{"role": "user", "content": "hi"}], endpoint="databricks-claude-opus-5-5",
                            max_tokens=2000, reasoning="medium")
    assert reply.content == "ok" and reply.notes == []
    assert bodies[0]["thinking"]["type"] == "enabled"
    assert bodies[1]["thinking"] == {"type": "adaptive"} and bodies[1]["output_config"] == {"effort": "medium"}
    assert "temperature" not in bodies[1]

    chat_with_tools([{"role": "user", "content": "again"}], endpoint="databricks-claude-opus-5-5",
                    max_tokens=2000, reasoning="medium")
    assert len(bodies) == 3 and bodies[2]["thinking"] == {"type": "adaptive"}


def test_a_model_that_refuses_temperature_runs_without_it_from_then_on(monkeypatch):
    from app.clients.model_serving import chat_with_tools

    bodies = _fake_client(monkeypatch, [NO_TEMPERATURE, OK])
    reply = chat_with_tools([{"role": "user", "content": "hi"}], endpoint="databricks-claude-opus-5-5")
    assert reply.content == "ok" and bodies[0]["temperature"] == 0.1 and "temperature" not in bodies[1]

    chat_with_tools([{"role": "user", "content": "again"}], endpoint="databricks-claude-opus-5-5")
    assert len(bodies) == 3 and "temperature" not in bodies[2]


def test_every_refused_parameter_is_dropped_in_turn_before_giving_up(monkeypatch):
    from app.clients.model_serving import ModelServingError, chat_with_tools

    bodies = _fake_client(monkeypatch, [OPUS_ADAPTIVE, RuntimeError("Extra inputs are not permitted: output_config"),
                                        NO_TEMPERATURE, OK])
    reply = chat_with_tools([{"role": "user", "content": "hi"}], endpoint="databricks-claude-opus-5-5",
                            reasoning="high")
    assert len(bodies) == 4 and reply.content == "ok" and "without one" in reply.notes[0]
    assert "thinking" not in bodies[3] and "temperature" not in bodies[3]

    _fake_client(monkeypatch, [RuntimeError("Rate limit exceeded")])
    with pytest.raises(ModelServingError, match="Rate limit"):
        chat_with_tools([{"role": "user", "content": "hi"}], endpoint="databricks-claude-opus-5-5")


def test_testing_a_model_reports_a_refusal_instead_of_raising(client, monkeypatch):
    _fake_client(monkeypatch, [RuntimeError("This endpoint databricks-gemini-2-5-flash is deprecated.")])
    r = client.post("/api/v1/assistant/models/test", json={"endpoint": "databricks-gemini-2-5-flash"})
    assert r.status_code == 200 and r.json()["ok"] is False and "deprecated" in r.json()["error"]
