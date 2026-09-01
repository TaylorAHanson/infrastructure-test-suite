"""The Assistant's tool-calling loop — a LangGraph StateGraph (ADR-0019).

This replaces the old keyword-routed two-mode ``/turn`` (``_resolve_mode``) with
a real tool-calling agent: the pinned model, given a **closed inventory of tool
schemas**, decides per turn which tool(s) to call; the graph executes them and
loops until the model answers. Nothing here widens what the Assistant can do —
the inventory is exactly the eight read/grounding tools of
:mod:`app.services.assistant_tools`, and it is **closed by omission** (ADR-0020):
there is deliberately no schema for a Target write, ``/admin/*``, Branding, or
Webhook action, so those are unreachable *by design*, not by a runtime refusal.

Behaviour is preserved from the pre-migration flow: capability selection is
contextual (a finding under triage is an RCA context, ADR-0012 §2; an explicit
``mode`` is honoured), the write-path still runs the GUI validator and refuses
R3 via :func:`app.services.authoring.build_draft`, and the read-path
(:func:`app.services.rca.suggest`) stays read-only and advisory. Grounding is
still done deterministically so a suggested run-as-role stays bounded to the
target's registered roles even when the model doesn't call the grounding tools.
The model is reached only through the injectable ``model_call`` seam, which
returns a JSON string — a ``{"tool_calls": [...]}`` envelope for a tool step, or
the final answer object otherwise — so the whole loop stays stubbable in tests.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, StateGraph
from sqlmodel import Session, select

from app.models import Target
from app.schemas.schemas import AssistantTurnIn
from app.services import assistant_tools, authoring, rca
from app.services.authoring import parse_json_object

logger = logging.getLogger(__name__)

ModelCall = Callable[[list[dict]], str]

#: Bound on model⇄tool round-trips per turn — a runaway loop still terminates.
MAX_ITERS = 6


# ---------------------------------------------------------------------------
# Tool inventory — closed by omission (ADR-0020)
# ---------------------------------------------------------------------------
def _search_sdk_surface(session, target, args):
    return assistant_tools.search_sdk_surface(str(args.get("query", "")),
                                              int(args.get("limit", 8) or 8))


def _search_targets(session, target, args):
    return assistant_tools.search_targets(session, str(args.get("query", "")),
                                          int(args.get("limit", 10) or 10))


def _search_roles(session, target, args):
    ref = args.get("target_ref") or args.get("target_id") or (target.id if target else "")
    return assistant_tools.search_roles(session, str(ref or ""))


def _search_examples(session, target, args):
    return assistant_tools.search_examples(session, str(args.get("query", "")),
                                           int(args.get("limit", 5) or 5))


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


def _operation_reference(session, target, args):
    return assistant_tools.operation_reference(str(args.get("service", "")),
                                               str(args.get("method", "")))


def _search_suites(session, target, args):
    return assistant_tools.search_suites(session, str(args.get("query", "")),
                                         int(args.get("limit", 10) or 10))


# ---------------------------------------------------------------------------
# Suite creation — proposal-only (issue #66, spec #56 user stories 12-13). The
# composition tools below (issue #63) all require an existing suite_id; this
# is the missing "start from nothing" counterpart, following the same
# eager-validate/never-auto-apply shape.
# ---------------------------------------------------------------------------
def _create_suite(session, target, args):
    from app.models import Suite, Test
    from app.services import suite_safety
    name = str(args.get("name", "")).strip()
    if not name:
        return {"error": "name is required"}
    test_ids = list(args.get("test_ids") or [])
    for tid in test_ids:
        if not session.get(Test, tid):
            return {"error": f"test {tid!r} not found"}
    deterministic_only = bool(args.get("deterministic_only", False))
    # Unpersisted stand-in Suite — only its deterministic_only flag is read, and
    # that is always supplied explicitly below, so a real row isn't needed yet.
    draft_suite = Suite(name=name, deterministic_only=deterministic_only)
    violation = suite_safety.check_deterministic_violation(
        session, draft_suite, test_ids, proposed_deterministic_only=deterministic_only)
    return {
        "name": name,
        "description": str(args.get("description", "") or ""),
        "tags": list(args.get("tags") or []),
        "proposed_test_ids": test_ids,
        "proposed_deterministic_only": deterministic_only,
        "deterministic_violation": violation,
        "job_scheduled": False,
        "requires_confirmation": True,
    }


# ---------------------------------------------------------------------------
# Suite composition tools — proposal-only (issue #63, spec #56 user stories 8-11)
# ---------------------------------------------------------------------------
def _compose_suite_add_test(session, target, args):
    from app.models import Suite, SuiteMember, Test
    from app.services import suite_safety
    suite_id = str(args.get("suite_id", ""))
    test_id = str(args.get("test_id", ""))
    suite = session.get(Suite, suite_id)
    if not suite:
        return {"error": f"suite {suite_id!r} not found"}
    test = session.get(Test, test_id)
    if not test:
        return {"error": f"test {test_id!r} not found"}
    existing = session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite_id)  # type: ignore[arg-type]
    ).all()
    current_ids = [m.test_id for m in existing]
    if test_id in current_ids:
        return {"error": f"test {test_id!r} is already a member of this suite"}
    proposed_ids = current_ids + [test_id]
    violation = suite_safety.check_deterministic_violation(session, suite, proposed_ids)
    job_scheduled = suite_safety.is_suite_referenced_by_nightly_job(session, suite_id)
    return {
        "suite_id": suite_id,
        "proposed_test_ids": proposed_ids,
        "proposed_deterministic_only": suite.deterministic_only,
        "deterministic_violation": violation,
        "job_scheduled": job_scheduled,
        "requires_confirmation": True,
    }


def _compose_suite_remove_test(session, target, args):
    from app.models import Suite, SuiteMember
    from app.services import suite_safety
    suite_id = str(args.get("suite_id", ""))
    test_id = str(args.get("test_id", ""))
    suite = session.get(Suite, suite_id)
    if not suite:
        return {"error": f"suite {suite_id!r} not found"}
    existing = session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite_id)  # type: ignore[arg-type]
    ).all()
    current_ids = [m.test_id for m in existing]
    if test_id not in current_ids:
        return {"error": f"test {test_id!r} is not a member of this suite"}
    proposed_ids = [tid for tid in current_ids if tid != test_id]
    job_scheduled = suite_safety.is_suite_referenced_by_nightly_job(session, suite_id)
    return {
        "suite_id": suite_id,
        "proposed_test_ids": proposed_ids,
        "proposed_deterministic_only": suite.deterministic_only,
        "deterministic_violation": None,
        "job_scheduled": job_scheduled,
        "requires_confirmation": True,
    }


def _compose_suite_reorder(session, target, args):
    from app.models import Suite, SuiteMember
    from app.services import suite_safety
    suite_id = str(args.get("suite_id", ""))
    new_order = list(args.get("test_ids") or [])
    suite = session.get(Suite, suite_id)
    if not suite:
        return {"error": f"suite {suite_id!r} not found"}
    existing = session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite_id)  # type: ignore[arg-type]
    ).all()
    current_ids = {m.test_id for m in existing}
    if set(new_order) != current_ids:
        return {"error": "test_ids must be a permutation of the suite's current member ids"}
    job_scheduled = suite_safety.is_suite_referenced_by_nightly_job(session, suite_id)
    return {
        "suite_id": suite_id,
        "proposed_test_ids": new_order,
        "proposed_deterministic_only": suite.deterministic_only,
        "deterministic_violation": None,
        "job_scheduled": job_scheduled,
        "requires_confirmation": True,
    }


def _compose_suite_toggle_det_only(session, target, args):
    from app.models import Suite, SuiteMember
    from app.services import suite_safety
    suite_id = str(args.get("suite_id", ""))
    new_value = bool(args.get("deterministic_only", False))
    suite = session.get(Suite, suite_id)
    if not suite:
        return {"error": f"suite {suite_id!r} not found"}
    existing = session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite_id)  # type: ignore[arg-type]
    ).all()
    current_ids = [m.test_id for m in existing]
    violation = suite_safety.check_deterministic_violation(
        session, suite, current_ids, proposed_deterministic_only=new_value)
    job_scheduled = suite_safety.is_suite_referenced_by_nightly_job(session, suite_id)
    return {
        "suite_id": suite_id,
        "proposed_test_ids": current_ids,
        "proposed_deterministic_only": new_value,
        "deterministic_violation": violation,
        "job_scheduled": job_scheduled,
        "requires_confirmation": True,
    }


#: name → adapter ``(session, target, arguments) -> result``. The single dispatch
#: table for what the model is allowed to invoke; nothing outside it is reachable.
TOOL_REGISTRY: dict[str, Callable[[Session, Optional[Target], dict], Any]] = {
    "search_sdk_surface": _search_sdk_surface,
    "search_targets": _search_targets,
    "search_roles": _search_roles,
    "search_examples": _search_examples,
    "search_tests": _search_tests,
    "run_history": _run_history,
    "commit_provenance_thread": _commit_provenance_thread,
    "failure_archive": _failure_archive,
    "operation_reference": _operation_reference,
    "search_suites": _search_suites,
    # Suite creation (propose-only, issue #66)
    "create_suite": _create_suite,
    # Suite composition (propose-only, issue #63)
    "compose_suite_add_test": _compose_suite_add_test,
    "compose_suite_remove_test": _compose_suite_remove_test,
    "compose_suite_reorder": _compose_suite_reorder,
    "compose_suite_toggle_det_only": _compose_suite_toggle_det_only,
}


def _function_schema(name: str, description: str, properties: dict, required: Optional[list] = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required or []}}}


#: OpenAI-style function schemas the pinned model chooses from. This list is the
#: whole surface the model can act through — see the module docstring on closure.
TOOL_SCHEMAS: list[dict] = [
    _function_schema("search_sdk_surface",
        "Search the live Databricks SDK/OpenAPI surface (minus the blocklist) for "
        "operations matching a plain-English query.",
        {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _function_schema("search_targets",
        "Find registered targets by free-text over name/workspace/host/tags.",
        {"query": {"type": "string"}, "limit": {"type": "integer"}}),
    _function_schema("search_roles",
        "List the assumable role-profiles a target has registered (never invents one).",
        {"target_ref": {"type": "string", "description": "target id or name"}},
        ["target_ref"]),
    _function_schema("search_examples",
        "Rank the worked-example library against an authoring intent.",
        {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _function_schema("search_tests",
        "Search existing Tests/Checks by name, description, operation, tags, and status "
        "(draft/active). Use to answer 'Do we have a test for X?' without authoring.",
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
    _function_schema("operation_reference",
        "Look up one SDK-surface operation (verb, stages, hints) by service.method.",
        {"service": {"type": "string"}, "method": {"type": "string"}},
        ["service", "method"]),
    _function_schema("search_suites",
        "List available Suites by name/description/tags — for grounding a run-submission "
        "proposal. Returns id, name, test_count, deterministic_only.",
        {"query": {"type": "string"}, "limit": {"type": "integer"}}),
    # Suite creation — returns a proposal only (issue #66)
    _function_schema("create_suite",
        "Propose creating a brand-new Suite with a name, description, tags, "
        "deterministic_only flag, and initial member test_ids (from search_tests — "
        "never invent one). Returns a proposal with a deterministic-only violation "
        "check. Never auto-applies; requires confirmation.",
        {"name": {"type": "string"}, "description": {"type": "string"},
         "tags": {"type": "array", "items": {"type": "string"}},
         "deterministic_only": {"type": "boolean"},
         "test_ids": {"type": "array", "items": {"type": "string"}}},
        ["name"]),
    # Suite composition tools — return proposals only (issue #63)
    _function_schema("compose_suite_add_test",
        "Propose adding a Test to a Suite. Returns a proposal with deterministic-only "
        "violation check and nightly-job warning. Never auto-applies; requires confirmation.",
        {"suite_id": {"type": "string"}, "test_id": {"type": "string"}},
        ["suite_id", "test_id"]),
    _function_schema("compose_suite_remove_test",
        "Propose removing a Test from a Suite. Returns a proposal with nightly-job "
        "warning. Never auto-applies; requires confirmation.",
        {"suite_id": {"type": "string"}, "test_id": {"type": "string"}},
        ["suite_id", "test_id"]),
    _function_schema("compose_suite_reorder",
        "Propose reordering Tests in a Suite. test_ids must be a permutation of the "
        "current member ids. Never auto-applies; requires confirmation.",
        {"suite_id": {"type": "string"},
         "test_ids": {"type": "array", "items": {"type": "string"}}},
        ["suite_id", "test_ids"]),
    _function_schema("compose_suite_toggle_det_only",
        "Propose toggling deterministic_only on a Suite. Refused if any member Test "
        "contains agentic Checks. Never auto-applies; requires confirmation.",
        {"suite_id": {"type": "string"},
         "deterministic_only": {"type": "boolean", "description": "new value for the flag"}},
        ["suite_id", "deterministic_only"]),
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
    """A grounding search that came back empty → offer to author one (story 7)."""
    if result:
        return []
    q = str(args.get("query", "")).strip()
    seed = f"Create a new test that {q}" if q else "Create a new test that…"
    return [{"kind": "author", "label": "Author this test", "seed": seed}]


def _na_search_targets(args: dict, result: Any) -> list[dict]:
    if result:
        return [{"kind": "navigate", "label": "View targets", "href": "/targets"}]
    return []


def _na_run_history(args: dict, result: Any) -> list[dict]:
    return [{"kind": "navigate", "label": "View run history", "href": "/runs"}]


def _na_whats_interesting(args: dict, result: Any) -> list[dict]:
    return [{"kind": "navigate", "label": "Open What's Interesting", "href": "/"}]


#: tool name → a builder ``(arguments, result) -> [NextAction]``. Keyed by the
#: same names as :data:`TOOL_REGISTRY` (a builder for a non-tool is meaningless);
#: enforced ``⊆ TOOL_REGISTRY`` by test so the button set stays registry-driven.
NEXT_ACTION_BUILDERS: dict[str, Callable[[dict, Any], list[dict]]] = {
    "search_sdk_surface": _na_author_when_empty,
    "search_examples": _na_author_when_empty,
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
    "You are the authoring capability of a Databricks validation Assistant. Use the "
    "provided tools to ground on the live SDK surface, worked examples, and the "
    "target's registered roles as needed. "
    "For a query turn (e.g. 'Do we have a test for X?', 'What happened last run?'), "
    "call the appropriate read tool(s), then reply with {\"answer\": \"<plain-English answer>\"}. "
    "For a meta, greeting, or chit-chat turn that isn't a request to author, query, or manage "
    "anything (e.g. 'hi', 'are you working?', 'what can you do?'), skip the tools and reply with "
    "{\"answer\": \"<a short, plain-English blurb of what you can help with>\"} — never a "
    "clarifying question or a Draft. "
    "For authoring, reply with a single JSON object: {\"name\", \"description\", "
    "\"operation\": {\"service\", \"method\", \"params\"}, \"assertion\", \"handler_key\", "
    "\"run_as_role\" (or null), \"counterfactual\": {\"assertion\", \"params\"} or null, "
    "\"clarifying_question\" (or null)}. Ground your operation on the tools; never invent a role."
)

_SUITE_TOOL_SYSTEM = (
    "You are the Suite capability of a Databricks validation Assistant, reachable from "
    "the Suites page. Use search_tests to find existing Tests by name/description when "
    "the user names them (never invent a test id), and search_suites if the user might "
    "mean an existing Suite. "
    "To create a brand-new Suite, call create_suite with name, description, tags, "
    "deterministic_only, and test_ids. To change a Suite already on screen (its id is in "
    "the grounding as suite_id), call compose_suite_add_test / compose_suite_remove_test / "
    "compose_suite_reorder / compose_suite_toggle_det_only instead. Every one of these "
    "tools only proposes a change — nothing is created or applied until the human "
    "confirms. After the tool responds, reply with a single JSON object: "
    "{\"answer\": \"<plain-English summary of the proposal, asking the user to confirm>\"}. "
    "For a plain question (no creation/change intended), just reply with {\"answer\": ...}."
)

#: Suite-mutating tool names reachable from the Suites page (issue #66/#63) — the
#: `_suite_turn` loop scans executed tool calls for one of these to surface as a
#: confirmable proposal, and maps each to the `action` the confirm endpoint expects.
_SUITE_ACTION_BY_TOOL = {
    "create_suite": "create",
    "compose_suite_add_test": "add_test",
    "compose_suite_remove_test": "remove_test",
    "compose_suite_reorder": "reorder",
    "compose_suite_toggle_det_only": "toggle_det_only",
}


def _suite_turn(session: Session, payload: AssistantTurnIn,
                model_call: Optional[ModelCall]) -> dict:
    """The Suites-page capability (issue #66): create a brand-new Suite, or compose
    one already on screen, via chat — grounded on search_tests/search_suites.

    Every mutating tool only proposes (issue #63's pattern, extended to creation);
    nothing is created or changed until the human confirms via
    ``POST /assistant/suite-compose/confirm``, which re-runs the safety gate.
    """
    ctx = payload.page_context
    intent = payload.turn
    grounding = {"suite_id": ctx.suite_id} if ctx.suite_id else {}
    init: _TurnState = {
        "messages": [{"role": "system", "content": _SUITE_TOOL_SYSTEM},
                     {"role": "user", "content": json.dumps({"intent": intent, "grounding": grounding})}],
        "executed": [], "pending": [], "plan": {}, "iters": 0,
    }
    graph = _build_graph(session, None, model_call)
    final = graph.invoke(init, {"recursion_limit": 2 * MAX_ITERS + 5})
    plan = final.get("plan") or {}
    executed = final.get("executed", [])

    suite_call = next((t for t in reversed(executed) if t["name"] in _SUITE_ACTION_BY_TOOL), None)
    proposal = None
    if suite_call is not None and "error" not in (suite_call["result"] or {}):
        proposal = {**suite_call["result"], "action": _SUITE_ACTION_BY_TOOL[suite_call["name"]]}

    return {
        "mode": "suite_authoring" if proposal else "query",
        "page": ctx.page,
        "answer": plan.get("answer"),
        "suite_proposal": proposal,
        "tool_calls": executed,
    }


_RUN_SUBMISSION_TOOL_SYSTEM = (
    "You are the run-submission capability of a Databricks validation Assistant. "
    "Use search_targets and search_suites to identify the target(s) and suite(s)/test(s) "
    "the user wants to run. When ready, reply with a single JSON object: "
    "{\"run_proposal\": {\"target_ids\": [...], \"suite_ids\": [...], \"test_ids\": [...], "
    "\"depth\": \"quick\" or \"full\", \"summary\": \"human-readable description\"}}. "
    "If you cannot identify required slots, reply with {\"clarifying_question\": \"...\"}. "
    "Never invent target or suite ids — only use ids returned by the search tools."
)


def _initial_messages(intent: str, grounding: dict) -> list[dict]:
    return [
        {"role": "system", "content": _AUTHORING_TOOL_SYSTEM},
        {"role": "user", "content": json.dumps({"intent": intent, "grounding": grounding})},
    ]



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
            messages = state["messages"] + [{"role": "assistant", "content": reply}]
            return {"messages": messages, "pending": tool_calls, "iters": iters + 1}
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
               grounding: dict, model_call: Optional[ModelCall]) -> tuple[dict, list[dict]]:
    """Run the loop; return ``(final_plan, executed_tool_calls)``."""
    app = _build_graph(session, target, model_call)
    init: _TurnState = {"messages": _initial_messages(intent, grounding),
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
    if payload.mode == "run_submission":
        return _run_submission_turn(session, payload, model_call)
    if payload.mode in ("authoring", "rca", "suite"):
        mode = payload.mode
    elif ctx.finding:
        mode = "rca"
    elif ctx.page == "suites":
        # The Suites page (issue #66): create-or-compose, never the Test compiler.
        mode = "suite"
    else:
        mode = "authoring"
    if mode == "rca":
        return _rca_turn(session, payload, model_call)
    if mode == "suite":
        return _suite_turn(session, payload, model_call)
    return _authoring_turn(session, payload, model_call)


def _authoring_turn(session: Session, payload: AssistantTurnIn,
                    model_call: Optional[ModelCall]) -> dict:
    ctx = payload.page_context
    target = _page_target(session, ctx)
    intent = payload.turn
    grounding = authoring._grounding(session, intent, target)
    plan, executed = _tool_loop(session, target, intent, grounding, model_call)
    # Query answer: model resolved the turn as a read/lookup, not an authoring request.
    if plan.get("answer") and "operation" not in plan and "name" not in plan:
        return {"mode": "query", "page": ctx.page, "answer": plan["answer"],
                "tool_calls": executed}
    # Deterministic guard (issue #112): the model gave nothing resolvable (no
    # model configured, or a degraded reply) *and* the turn itself is a
    # greeting/meta/chit-chat turn — abstain into the capability blurb instead
    # of a clarifying-question Draft. The model-free safety net; #111's
    # weak-surface-match abstention (inside build_draft) remains the fallback
    # for anything this classifier misses.
    if "operation" not in plan and "name" not in plan and authoring.is_conversational_turn(intent):
        return {"mode": "query", "page": ctx.page,
                "answer": authoring.CONVERSATIONAL_ANSWER, "tool_calls": executed}
    proposal = authoring.build_draft(session, intent, plan, bool(plan), target,
                                     grounding=grounding)
    return {"mode": "authoring", "page": ctx.page, "draft": proposal.to_dict(),
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


def _run_submission_turn(session: Session, payload: AssistantTurnIn,
                         model_call: Optional[ModelCall]) -> dict:
    """Propose a Run (suggest-then-human-confirms) via the tool-calling loop.

    The model calls search_targets / search_suites to ground its proposal, then
    returns a ``run_proposal`` JSON object. Nothing is submitted here — the user
    must confirm by calling ``POST /api/v1/assistant/run``, which routes through
    the same ``run_service.submit_request()`` path as the manual ``POST /runs``
    endpoint (guardrail reuse, issue #64 AC #2).
    """
    intent = payload.turn
    init: _TurnState = {
        "messages": [{"role": "system", "content": _RUN_SUBMISSION_TOOL_SYSTEM},
                     {"role": "user", "content": intent}],
        "executed": [], "pending": [], "plan": {}, "iters": 0,
    }
    graph = _build_graph(session, None, model_call)
    final = graph.invoke(init, {"recursion_limit": 2 * MAX_ITERS + 5})
    plan = final.get("plan") or {}
    executed = final.get("executed", [])

    run_proposal = plan.get("run_proposal") or None
    clarifying_question = plan.get("clarifying_question") or None

    return {
        "mode": "run_submission",
        "page": payload.page_context.page,
        "run_proposal": run_proposal,
        "clarifying_question": clarifying_question,
        "tool_calls": executed,
    }


def _page_target(session: Session, ctx) -> Optional[Target]:
    tid = ctx.target_id or (ctx.target_ids[0] if ctx.target_ids else None)
    return session.get(Target, tid) if tid else None
