"""The Assistant's tool-calling loop — a LangGraph StateGraph (ADR-0019).

The pinned model, given a **closed inventory of tool schemas**, decides per turn
which tool(s) to call; the graph executes them and loops until the model
answers. The inventory is exactly the read tools of
:mod:`app.services.assistant_tools`, and it is **closed by omission**
(ADR-0020): there is deliberately no schema for a Test or Target write,
``/admin/*``, Branding, or Webhook action, so those are unreachable *by design*,
not by a runtime refusal.

Capability selection is contextual (a finding under triage is an RCA context,
ADR-0012 §2; an explicit ``mode`` is honoured). An answer comes back as
``query``; a request to write a Test comes back as ``authoring`` with no draft —
the shelf hands it to the script editor, whose agent writes the script — and
the read-path (:func:`app.services.rca.suggest`) stays read-only and advisory.
The model is reached only through the injectable ``model_call`` seam, which
returns a JSON string — a ``{"tool_calls": [...]}`` envelope for a tool step, or
the final answer object otherwise — so the whole loop stays stubbable in tests.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, StateGraph
from sqlmodel import Session

from app.models import Target
from app.schemas.schemas import AssistantTurnIn
from app.services import assistant_tools, rca
from app.services.assistant import parse_json_object

logger = logging.getLogger(__name__)

ModelCall = Callable[[list[dict]], str]

#: Bound on model⇄tool round-trips per turn — a runaway loop still terminates.
MAX_ITERS = 6


# ---------------------------------------------------------------------------
# Tool inventory — closed by omission (ADR-0020)
# ---------------------------------------------------------------------------
def _search_targets(session, target, args):
    return assistant_tools.search_targets(session, str(args.get("query", "")),
                                          int(args.get("limit", 10) or 10))


def _search_roles(session, target, args):
    ref = args.get("target_ref") or args.get("target_id") or (target.id if target else "")
    return assistant_tools.search_roles(session, str(ref or ""))


def _run_history(session, target, args):
    return assistant_tools.run_history(session, check_id=args.get("check_id"),
                                       target_id=args.get("target_id"),
                                       limit=int(args.get("limit", 20) or 20))


def _commit_provenance_thread(session, target, args):
    return assistant_tools.commit_provenance_thread(
        session, check_id=str(args.get("check_id", "")), target_id=args.get("target_id"))


def _failure_archive(session, target, args):
    return assistant_tools.failure_archive(session, bucket=args.get("bucket"),
                                           stage=args.get("stage"),
                                           limit=int(args.get("limit", 20) or 20))


def _search_tests(session, target, args):
    return assistant_tools.search_tests(session, str(args.get("query", "")),
                                        args.get("status"), int(args.get("limit", 10) or 10))


def _search_suites(session, target, args):
    return assistant_tools.search_suites(session, str(args.get("query", "")),
                                         int(args.get("limit", 10) or 10))


#: name → adapter ``(session, target, arguments) -> result``. The single dispatch
#: table for what the model is allowed to invoke; nothing outside it is reachable.
TOOL_REGISTRY: dict[str, Callable[[Session, Optional[Target], dict], Any]] = {
    "search_targets": _search_targets,
    "search_roles": _search_roles,
    "search_tests": _search_tests,
    "run_history": _run_history,
    "commit_provenance_thread": _commit_provenance_thread,
    "failure_archive": _failure_archive,
    "search_suites": _search_suites,
}


def _function_schema(name: str, description: str, properties: dict, required: Optional[list] = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required or []}}}


#: OpenAI-style function schemas the pinned model chooses from. This list is the
#: whole surface the model can act through — see the module docstring on closure.
TOOL_SCHEMAS: list[dict] = [
    _function_schema("search_targets",
        "Find registered targets by free-text over name/workspace/host/tags.",
        {"query": {"type": "string"}, "limit": {"type": "integer"}}),
    _function_schema("search_roles",
        "List the assumable role-profiles a target has registered (never invents one).",
        {"target_ref": {"type": "string", "description": "target id or name"}},
        ["target_ref"]),
    _function_schema("search_tests",
        "Search existing Tests and their checks by name, description, the SDK calls they "
        "make, tags, script text, and status (draft/active). Use to answer 'Do we have a "
        "test for X?'.",
        {"query": {"type": "string"}, "status": {"type": "string",
         "description": "filter by status: 'draft', 'active', or 'all' (default all)"},
         "limit": {"type": "integer"}}),
    _function_schema("run_history",
        "Recent CheckRuns, newest first; callable with no ids for the latest runs.",
        {"check_id": {"type": "string"}, "target_id": {"type": "string"},
         "limit": {"type": "integer"}}),
    _function_schema("commit_provenance_thread",
        "The last-green → first-bad thread for a (check, target).",
        {"check_id": {"type": "string"}, "target_id": {"type": "string"}}, ["check_id"]),
    _function_schema("failure_archive",
        "Prior occurrences of a failure bucket @ stage across runs.",
        {"bucket": {"type": "string"}, "stage": {"type": "string"},
         "limit": {"type": "integer"}}),
    _function_schema("search_suites",
        "List available Suites by name/description/tags. Returns id, name and test_count.",
        {"query": {"type": "string"}, "limit": {"type": "integer"}}),
]


def dispatch_tool(session: Session, target: Optional[Target], name: str, args: dict) -> Any:
    """Execute one model-requested tool, resiliently. Unknown → an error result."""
    adapter = TOOL_REGISTRY.get(name)
    if adapter is None:
        return {"error": f"unknown tool '{name}'"}
    try:
        return adapter(session, target, args or {})
    except Exception as exc:  # noqa: BLE001 - a tool error is data for the model, never fatal
        logger.warning("Assistant tool '%s' failed: %s", name, exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Dynamic next-action buttons — derived from which tools ran (spec #56 story 6/7)
# ---------------------------------------------------------------------------
# After an answer, the shelf offers follow-up buttons. They are **driven
# generically by the tool registry**, not a hardcoded button set per capability:
# each tool that ran contributes its own follow-ups from its *result*, keyed by
# the same tool name as ``TOOL_REGISTRY``. A tool with no builder contributes
# nothing; adding a tool + a builder is all it takes to add its actions — so this
# extends unchanged as the inventory grows (Suite/Run tools, #63/#64). Each
# action is a small kind-tagged descriptor the frontend renders and wires:
#   ``author``   — seed the shelf input with a starting sentence (openWithPrompt);
#   ``navigate`` — route the main pane to ``href``.
def _na_author_when_empty(args: dict, result: Any) -> list[dict]:
    """A test search that came back empty → offer to author one (story 7)."""
    if result:
        return []
    q = str(args.get("query", "")).strip()
    seed = f"Create a new test that {q}" if q else "Create a new test that…"
    return [{"kind": "author", "label": "Author this test", "seed": seed}]


def _na_search_targets(args: dict, result: Any) -> list[dict]:
    if result:
        return [{"kind": "navigate", "label": "View targets", "href": "/settings/targets"}]
    return []


def _na_run_history(args: dict, result: Any) -> list[dict]:
    return [{"kind": "navigate", "label": "View run history", "href": "/runs"}]


def _na_whats_interesting(args: dict, result: Any) -> list[dict]:
    return [{"kind": "navigate", "label": "Open What's Interesting", "href": "/"}]


#: tool name → a builder ``(arguments, result) -> [NextAction]``. Keyed by the
#: same names as :data:`TOOL_REGISTRY` (a builder for a non-tool is meaningless);
#: enforced ``⊆ TOOL_REGISTRY`` by test so the button set stays registry-driven.
NEXT_ACTION_BUILDERS: dict[str, Callable[[dict, Any], list[dict]]] = {
    "search_tests": _na_author_when_empty,
    "search_targets": _na_search_targets,
    "run_history": _na_run_history,
    "commit_provenance_thread": _na_whats_interesting,
    "failure_archive": _na_whats_interesting,
}


def build_next_actions(executed: list[dict]) -> list[dict]:
    """Derive follow-up-action descriptors from the tools a turn actually ran.

    Walks the executed tool calls, asks each tool's builder (if any) what
    follow-ups its result warrants, and returns them deduped in first-seen order.
    Generic by construction — no per-capability branching (spec #56 story 6/7).
    """
    actions: list[dict] = []
    seen: set = set()
    for call in executed or []:
        builder = NEXT_ACTION_BUILDERS.get(str(call.get("name", "")))
        if builder is None:
            continue
        for action in builder(call.get("arguments") or {}, call.get("result")):
            key = (action.get("kind"), action.get("href"), action.get("seed"),
                   action.get("label"))
            if key in seen:
                continue
            seen.add(key)
            actions.append(action)
    return actions


# ---------------------------------------------------------------------------
# The StateGraph tool-calling loop
# ---------------------------------------------------------------------------
class _TurnState(TypedDict, total=False):
    messages: list[dict]
    executed: list[dict]  # tool-call transparency (name/arguments/result)
    pending: list[dict]   # tool calls the model asked for this step
    plan: dict            # the model's final answer object
    iters: int


_AUTHORING_TOOL_SYSTEM = (
    "You are a Databricks validation Assistant. Use the provided read tools to look up "
    "existing tests and suites, targets and their registered roles, and run history. "
    "For a question (e.g. 'Do we have a test for X?', 'What happened last run?'), call the "
    "appropriate tool(s), then reply with {\"answer\": \"<plain-English answer>\"}. "
    "For a meta, greeting, or chit-chat turn that isn't a request to write, look up, or manage "
    "anything (e.g. 'hi', 'are you working?', 'what can you do?'), skip the tools and reply with "
    "{\"answer\": \"<a short, plain-English blurb of what you can help with>\"}. "
    "For a request to write or change a test, reply with {\"author\": true, \"answer\": "
    "\"<one short sentence on what the test should check>\"}: the test editor writes the "
    "script, so never write one here."
)

#: Deterministic patterns for a meta / greeting / chit-chat turn — not a request
#: to write, look up, or manage anything (issue #112). This is abstention *inside*
#: the authoring turn (ADR-0019 §6), never a keyword classifier at the router:
#: the router still picks a capability from page context alone. It exists as
#: the model-free (or degraded-model) safety net — a live model is nudged by
#: :data:`_AUTHORING_TOOL_SYSTEM` to recognise the same turns itself and reply
#: with ``{"answer": ...}`` directly.
_CONVERSATIONAL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"^\s*(hi|hello|hey|howdy|yo|greetings)\b",
    r"\bhow('s| is| are) it going\b",
    r"\bhow are you\b",
    r"\bare you (working|there|online|up|alive)\b",
    r"\bwhat can you do\b",
    r"\bwhat do you do\b",
    r"\bwho are you\b",
    r"\bwhat (is|are) you\b",
    r"^\s*(thanks|thank you|ok|okay|cool|nice)\s*[.!?]*\s*$",
)]

#: The plain-English capability blurb a conversational turn gets back
#: (mode="query") — see :func:`is_conversational_turn`.
CONVERSATIONAL_ANSWER = (
    "Yes — I'm working. I can check whether a test already covers something, "
    "explain why a check regressed, look up runs, targets and suites, or open "
    "the test editor to write a new test. Just tell me what you'd like to do."
)


def is_conversational_turn(intent: str) -> bool:
    """True for a greeting/meta/chit-chat turn that isn't a request to write a test.

    A deterministic pattern match, not the router's capability choice (that
    stays context-only, ADR-0019 §4) — this only decides, *once a turn has
    already landed in authoring*, whether a turn the model didn't resolve should
    get :data:`CONVERSATIONAL_ANSWER` rather than a hand-off to the test editor.
    """
    text = (intent or "").strip()
    return bool(text) and any(p.search(text) for p in _CONVERSATIONAL_PATTERNS)


def _initial_messages(intent: str) -> list[dict]:
    return [
        {"role": "system", "content": _AUTHORING_TOOL_SYSTEM},
        {"role": "user", "content": json.dumps({"intent": intent})},
    ]



def _tool_calling_turn(content: Any, calls: list[dict]) -> dict:
    """The model's tool-calling turn in wire shape. Every tool result that follows
    must answer a tool call listed here, by id, or the endpoint rejects the request."""
    return {"role": "assistant", "content": content if isinstance(content, str) else "",
            "tool_calls": [{"id": c["id"], "type": "function",
                            "function": {"name": str(c.get("name", "")),
                                         "arguments": json.dumps(c.get("arguments") or {}, default=str)}}
                           for c in calls]}


def _build_graph(session: Session, target: Optional[Target], model_call: Optional[ModelCall]):
    """Compile the agent⇄tools StateGraph for one turn (closures bind the seam)."""

    def agent(state: _TurnState) -> dict:
        iters = state.get("iters", 0)
        if model_call is None or iters >= MAX_ITERS:
            return {"pending": [], "iters": iters}
        try:
            reply = model_call(state["messages"])
        except Exception:  # noqa: BLE001 - model is best-effort; end and fall back
            return {"pending": [], "plan": {}, "iters": iters + 1}
        parsed = parse_json_object(reply)
        tool_calls = parsed.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            calls = [{**tc, "id": tc.get("id") or f"call_{iters}_{i}"}
                     for i, tc in enumerate(tool_calls) if isinstance(tc, dict)]
            messages = state["messages"] + [_tool_calling_turn(parsed.get("content"), calls)]
            return {"messages": messages, "pending": calls, "iters": iters + 1}
        return {"plan": parsed, "pending": [], "iters": iters + 1}

    def tools(state: _TurnState) -> dict:
        executed = list(state.get("executed", []))
        messages = list(state["messages"])
        for tc in state.get("pending", []):
            name = str(tc.get("name", ""))
            args = tc.get("arguments") or {}
            result = dispatch_tool(session, target, name, args)
            executed.append({"name": name, "arguments": args, "result": result})
            messages.append({"role": "tool", "name": name,
                             "tool_call_id": tc.get("id", ""),
                             "content": json.dumps(result, default=str)})
        return {"messages": messages, "executed": executed, "pending": []}

    graph = StateGraph(_TurnState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent", lambda s: "tools" if s.get("pending") else "end",
        {"tools": "tools", "end": END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def _tool_loop(session: Session, target: Optional[Target], intent: str,
               model_call: Optional[ModelCall]) -> tuple[dict, list[dict]]:
    """Run the loop; return ``(final_plan, executed_tool_calls)``."""
    app = _build_graph(session, target, model_call)
    init: _TurnState = {"messages": _initial_messages(intent),
                        "executed": [], "pending": [], "plan": {}, "iters": 0}
    final = app.invoke(init, {"recursion_limit": 2 * MAX_ITERS + 5})
    return (final.get("plan") or {}), final.get("executed", [])


# ---------------------------------------------------------------------------
# Turn entry — contextual capability selection (no keyword classifier)
# ---------------------------------------------------------------------------
def run_agent_turn(session: Session, payload: AssistantTurnIn,
                   model_call: Optional[ModelCall] = None) -> dict:
    """Route one turn through the tool-calling loop (authoring) or RCA read-path.

    Capability is chosen from context, not keywords (ADR-0019): an explicit
    ``mode`` wins; otherwise a finding under triage is an RCA context and
    everything else is authoring.
    """
    ctx = payload.page_context
    if payload.mode in ("authoring", "rca"):
        mode = payload.mode
    elif ctx.finding:
        mode = "rca"
    else:
        mode = "authoring"
    if mode == "rca":
        return _rca_turn(session, payload, model_call)
    return _authoring_turn(session, payload, model_call)


def _authoring_turn(session: Session, payload: AssistantTurnIn,
                    model_call: Optional[ModelCall]) -> dict:
    """Answer the turn (``query``) or hand a request to write a Test to the editor.

    ``authoring`` carries no draft: the shelf opens the script editor with the
    turn as its first ask, plus the model's optional one-line ``answer``.
    """
    ctx = payload.page_context
    target = _page_target(session, ctx)
    intent = payload.turn
    plan, executed = _tool_loop(session, target, intent, model_call)
    answer = plan.get("answer")
    answer = answer if isinstance(answer, str) and answer.strip() else None
    if not plan.get("author"):
        if answer:
            return {"mode": "query", "page": ctx.page, "answer": answer, "tool_calls": executed}
        # Deterministic guard (issue #112): the model gave nothing resolvable (no
        # model configured, or a degraded reply) *and* the turn itself is a
        # greeting/meta/chit-chat turn — answer with the capability blurb rather
        # than opening the editor on "hi".
        if is_conversational_turn(intent):
            return {"mode": "query", "page": ctx.page, "answer": CONVERSATIONAL_ANSWER,
                    "tool_calls": executed}
    return {"mode": "authoring", "page": ctx.page, "answer": answer,
            "tool_calls": executed, "next_actions": build_next_actions(executed)}


def _rca_turn(session: Session, payload: AssistantTurnIn,
              model_call: Optional[ModelCall]) -> dict:
    ctx = payload.page_context
    finding = ctx.finding or {}
    check_id = finding.get("check_id")
    if not check_id:
        return {"mode": "rca", "page": ctx.page, "tool_calls": [], "next_actions": [],
                "clarifying_question": "Which finding should I look at? "
                                       "Open a failing check to give me its context."}
    target_id = finding.get("target_id") or ctx.target_id
    suggestion = rca.suggest(session, check_id=check_id, target_id=target_id,
                             turn=payload.turn, model_call=model_call)
    # Surface the read tools the RCA path used (transparency, additive).
    evidence = suggestion.evidence or {}
    executed = [
        {"name": "commit_provenance_thread",
         "arguments": {"check_id": check_id, "target_id": target_id},
         "result": evidence.get("commit_provenance_thread")},
        {"name": "run_history",
         "arguments": {"check_id": check_id, "target_id": target_id},
         "result": evidence.get("run_history")},
    ]
    return {"mode": "rca", "page": ctx.page, "rca": suggestion.to_dict(),
            "tool_calls": executed, "next_actions": build_next_actions(executed)}


def _page_target(session: Session, ctx) -> Optional[Target]:
    tid = ctx.target_id or (ctx.target_ids[0] if ctx.target_ids else None)
    return session.get(Target, tid) if tid else None
