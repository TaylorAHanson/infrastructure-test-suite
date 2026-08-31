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
    "For authoring, reply with a single JSON object: {\"name\", \"description\", "
    "\"operation\": {\"service\", \"method\", \"params\"}, \"assertion\", \"handler_key\", "
    "\"run_as_role\" (or null), \"counterfactual\": {\"assertion\", \"params\"} or null, "
    "\"clarifying_question\" (or null)}. Ground your operation on the tools; never invent a role."
)

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
    mode = payload.mode if payload.mode in ("authoring", "rca") else (
        "rca" if ctx.finding else "authoring")
    if mode == "rca":
        return _rca_turn(session, payload, model_call)
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
    proposal = authoring.build_draft(session, intent, plan, bool(plan), target,
                                     grounding=grounding)
    return {"mode": "authoring", "page": ctx.page, "draft": proposal.to_dict(),
            "tool_calls": executed}


def _rca_turn(session: Session, payload: AssistantTurnIn,
              model_call: Optional[ModelCall]) -> dict:
    ctx = payload.page_context
    finding = ctx.finding or {}
    check_id = finding.get("check_id")
    if not check_id:
        return {"mode": "rca", "page": ctx.page, "tool_calls": [],
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
            "tool_calls": executed}


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
