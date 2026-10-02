"""The test-editor agent: a multi-turn coding assistant for one Test script.

It works beside the human in the editor. Each turn it sees the conversation, the
current editor buffer, the selected target and the last dry-run result, and can:

* look things up — the Databricks SDK surface and docstrings, the docs sites,
  other Tests' scripts, run history and the failure archive;
* look at the real workspace — read-only SDK calls and read-only SQL, as the
  target's identity, through the same safety gate as a run;
* check its work — static validation and a quick (read-only) dry run;
* propose a complete script with :func:`propose_script`. A proposal is never
  applied: the human reviews the diff and applies or discards it.

There is no tool that saves, activates, deletes or runs anything for real.
The model is reached through the injectable ``model_call`` seam so tests stub it.
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from sqlmodel import Session, select

from app.clients.model_serving import ModelReply, ModelServingError, ToolCall
from app.core.enums import Depth, RequestSource, RiskTier
from app.models import CheckRun, Target, Test
from app.script_runtime import sdk_catalog
from app.script_runtime.analysis import analyze
from app.script_runtime.runner import _sql_operation, effective_verb
from app.services import assistant_tools, script_tests

logger = logging.getLogger(__name__)

EditorModel = Callable[[list[dict], list[dict]], ModelReply]

MAX_ITERS = 14
MAX_TOOL_CHARS = 9000
MAX_HISTORY = 24
DOC_HOSTS = ("docs.databricks.com", "learn.microsoft.com", "docs.gcp.databricks.com",
             "databricks-sdk-py.readthedocs.io", "api-docs.databricks.com", "kb.databricks.com")


@dataclass
class EditorState:
    session: Session
    target: Optional[Target]
    buffer: str
    test: Optional[Test]
    proposal: Optional[dict] = None
    dry_run: Optional[dict] = None
    steps: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def _clip(value: Any, limit: int = MAX_TOOL_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"\n… [truncated {len(text) - limit} chars]"


def _t_search_sdk(st: EditorState, args: dict) -> Any:
    return sdk_catalog.search(str(args.get("query", "")), limit=int(args.get("limit") or 12))


def _t_describe_sdk(st: EditorState, args: dict) -> Any:
    out = sdk_catalog.describe(str(args.get("service", "")), args.get("method") or None)
    if "doc" in out:
        out["doc"] = out["doc"][:6000]
    if "methods" in out and isinstance(out["methods"], list):
        out["methods"] = [{k: m[k] for k in ("method", "verb", "tier", "summary")} for m in out["methods"]]
    return out


def _t_validate(st: EditorState, args: dict) -> Any:
    return analyze(str(args.get("script") or st.buffer))


def _t_propose(st: EditorState, args: dict) -> Any:
    script = str(args.get("script") or "")
    if not script.strip():
        return {"error": "propose_script needs the complete script"}
    analysis = analyze(script)
    st.proposal = {"script": script, "summary": str(args.get("summary") or ""), "analysis": analysis}
    return {"recorded": True, "ok": analysis["ok"], "errors": analysis["errors"],
            "warnings": analysis["warnings"], "checks": [c["name"] for c in analysis["checks"]],
            "max_risk_tier": analysis["max_risk_tier"],
            "note": ("The human will see this as a diff to apply." if analysis["ok"] else
                     "This proposal can't be saved yet — fix the errors and propose again.")}


def _condense_dry_run(out: dict) -> dict:
    result = out.get("result") or {}
    return {
        "error": result.get("error") and {k: result["error"].get(k) for k in ("kind", "message", "line")},
        "checks": [{"name": c["name"], "outcome": c["outcome"], "stage": c.get("stage"),
                    "bucket": c.get("bucket"), "message": (c.get("message") or "")[:400],
                    "line": c.get("line"), "evidence": _clip(c.get("evidence") or {}, 800),
                    "calls": [{k: x.get(k) for k in ("op", "outcome", "tier", "message", "response_excerpt")}
                              for x in (c.get("calls") or [])[:8]]}
                   for c in result.get("checks", [])],
        "output": (result.get("output") or "")[-1500:],
        "logs": (result.get("logs") or [])[-20:],
        "duration_ms": result.get("duration_ms"),
        "analysis_errors": (out.get("analysis") or {}).get("errors"),
    }


def _t_dry_run(st: EditorState, args: dict) -> Any:
    if st.target is None:
        return {"error": "no target is selected in the editor; ask the human to pick one"}
    script = str(args.get("script") or (st.proposal or {}).get("script") or st.buffer)
    try:
        out = script_tests.dry_run(st.session, script=script, target=st.target, depth=Depth.QUICK,
                                   actor="assistant", request_source=RequestSource.API,
                                   run_as_role=st.test.run_as_role if st.test else None)
    except script_tests.TargetBusy as exc:
        return {"error": str(exc)}
    st.dry_run = {**out, "script": script}
    return {"depth": "quick (read calls only; mutating calls are skipped)", **_condense_dry_run(out)}


def _client(st: EditorState, role: Optional[str] = None):
    from types import SimpleNamespace

    from app.services.run_service import _demo_mode

    if _demo_mode():
        return None
    view = script_tests._target_view(st.target) if st.target else None
    return script_tests.client_factory(view or SimpleNamespace(name="app", identity_handle=None, host=None))(role)


def _gate_read(st: EditorState, op) -> Optional[str]:
    from app.core.enums import SafetyClass
    from app.services.safety import evaluate_operation_safety

    # With no target selected, reads go to the app's own workspace, capped as read-only.
    decision = evaluate_operation_safety(op, st.target.safety_class if st.target else SafetyClass.READ_ONLY,
                                         request_source=RequestSource.API,
                                         target_name=st.target.name if st.target else None)
    if not decision.allowed:
        return decision.reason
    if decision.effective_tier != RiskTier.R0:
        return f"{op.service}.{op.method} is {decision.effective_tier.value}; the assistant only makes read calls"
    return None


def _t_sdk_call(st: EditorState, args: dict) -> Any:
    from app.core.operations import Operation
    from app.script_runtime.runner import classify_exception

    service, method = str(args.get("service", "")), str(args.get("method", ""))
    kwargs = dict(args.get("kwargs") or {})
    if sdk_catalog.get_method(service, method) is None:
        return {"error": f"w.{service}.{method} is not a Databricks SDK method"}
    if service == "statement_execution":
        return {"error": "use run_sql for SQL"}
    op = Operation(service=service, method=method, verb=effective_verb(service, method, kwargs))
    refused = _gate_read(st, op)
    if refused:
        return {"error": refused}
    client = _client(st)
    if client is None:
        return {"demo": True, "note": "demo mode: no workspace is connected, so there is no live data"}
    try:
        fn = getattr(getattr(client, service), method)
        value = sdk_catalog.to_jsonable(fn(**sdk_catalog.coerce_kwargs(fn, kwargs)))
    except Exception as exc:  # noqa: BLE001
        return {"error": classify_exception(exc)}
    if isinstance(value, list):
        return {"count": len(value), "items": value[:25]}
    return value


def _t_run_sql(st: EditorState, args: dict) -> Any:
    from app.core.config import settings

    statement = str(args.get("statement", ""))
    op = _sql_operation(statement)
    if op.verb != "GET":
        return {"error": "run_sql only runs read-only statements (SELECT, SHOW, DESCRIBE, …)"}
    refused = _gate_read(st, op)
    if refused:
        return {"error": refused}
    client = _client(st)
    if client is None:
        return {"demo": True, "note": "demo mode: no workspace is connected, so there is no live data"}
    try:
        wid = args.get("warehouse_id")
        if not wid and st.target is not None:
            wid = (st.target.tags or {}).get("warehouse_id")
        if not wid and st.target is None:
            wid = settings.DATABRICKS_WAREHOUSE_ID
        if not wid:
            whs = list(client.warehouses.list())
            running = [w for w in whs if str(getattr(w.state, "value", w.state)) == "RUNNING"] or whs
            if not running:
                return {"error": "no SQL warehouse on this workspace"}
            wid = running[0].id
        resp = client.statement_execution.execute_statement(
            statement=statement, warehouse_id=wid, wait_timeout="30s",
            catalog=args.get("catalog"), schema=args.get("schema"))
        data = sdk_catalog.to_jsonable(resp)
    except Exception as exc:  # noqa: BLE001
        from app.script_runtime.runner import classify_exception

        return {"error": classify_exception(exc)}
    status = data.get("status") or {}
    if status.get("state") not in (None, "SUCCEEDED"):
        return {"error": (status.get("error") or {}).get("message") or status.get("state")}
    cols = [c.get("name") for c in (((data.get("manifest") or {}).get("schema") or {}).get("columns") or [])]
    rows = ((data.get("result") or {}).get("data_array")) or []
    return {"warehouse_id": wid, "columns": cols, "rows": rows[:50], "row_count": len(rows)}


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|nav|header|footer|svg)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>|</tr>|</pre>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _t_read_docs(st: EditorState, args: dict) -> Any:
    import httpx

    url = str(args.get("url", ""))
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in DOC_HOSTS):
        return {"error": f"read_docs only fetches documentation sites: {', '.join(DOC_HOSTS)}"}
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "infrastructure-test-suite/editor-agent"})
    except httpx.HTTPError as exc:
        return {"error": f"could not fetch {url}: {exc}"}
    if resp.status_code >= 400:
        return {"error": f"HTTP {resp.status_code} for {url}"}
    final_host = (resp.url.host or "").lower()
    if not any(final_host == h or final_host.endswith("." + h) for h in DOC_HOSTS):
        return {"error": f"{url} redirected off the documentation sites"}
    return {"url": str(resp.url), "text": _strip_html(resp.text)[:12000]}


def _t_get_test_script(st: EditorState, args: dict) -> Any:
    test = st.session.get(Test, str(args.get("test_id", "")))
    if test is None:
        return {"error": "test not found"}
    script, converted = script_tests.script_for(st.session, test)
    return {"test_id": test.id, "name": test.name, "converted_from_structured": converted, "script": script}


def _t_search_tests(st: EditorState, args: dict) -> Any:
    return assistant_tools.search_tests(st.session, str(args.get("query", "")), None,
                                        int(args.get("limit") or 10))


def _t_targets(st: EditorState, args: dict) -> Any:
    out = assistant_tools.search_targets(st.session, str(args.get("query", "")), 20)
    return out


def _t_roles(st: EditorState, args: dict) -> Any:
    ref = args.get("target_id") or (st.target.id if st.target else "")
    return assistant_tools.search_roles(st.session, str(ref or ""))


def _t_recent_results(st: EditorState, args: dict) -> Any:
    test_id = args.get("test_id") or (st.test.id if st.test else None)
    if not test_id:
        return {"error": "this test hasn't been saved yet, so it has no run history"}
    stmt = select(CheckRun).where(CheckRun.test_id == test_id)
    if st.target is not None and not args.get("all_targets"):
        stmt = stmt.where(CheckRun.target_id == st.target.id)
    runs = st.session.exec(stmt.order_by(CheckRun.created_at.desc()).limit(40)).all()  # type: ignore[union-attr]
    from app.models import Check

    names: dict[str, str] = {}
    out = []
    for r in runs:
        if r.check_id not in names:
            c = st.session.get(Check, r.check_id)
            names[r.check_id] = c.display_name if c else r.check_id
        out.append({"check": names[r.check_id], "verdict": r.verdict.value, "stage": r.failure_stage,
                    "bucket": r.failure_bucket, "message": (r.message or "")[:300],
                    "target_id": r.target_id, "depth": r.depth.value, "at": str(r.created_at)})
    return out


def _t_failure_archive(st: EditorState, args: dict) -> Any:
    return assistant_tools.failure_archive(st.session, bucket=args.get("bucket"), stage=args.get("stage"),
                                           limit=int(args.get("limit") or 20))


def _fn(name: str, description: str, properties: dict, required: Optional[list] = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required or []}}}


_S = {"type": "string"}
TOOLS: dict[str, tuple[Callable[[EditorState, dict], Any], dict]] = {
    "search_sdk": (_t_search_sdk, _fn(
        "search_sdk", "Search the Databricks Python SDK for methods matching a description "
        "(e.g. 'list unity catalog grants'). Returns w.<service>.<method> signatures, verb and risk tier.",
        {"query": _S, "limit": {"type": "integer"}}, ["query"])),
    "describe_sdk": (_t_describe_sdk, _fn(
        "describe_sdk", "Full docstring and signature for one SDK method (parameters, return type), or the "
        "method index of a service when method is omitted. Use before calling any method you haven't verified.",
        {"service": _S, "method": _S}, ["service"])),
    "sdk_call": (_t_sdk_call, _fn(
        "sdk_call", "Make one READ-ONLY SDK call on the selected target as its identity, to see real response "
        "shapes and data (e.g. what fields a cluster has). Mutating calls are refused.",
        {"service": _S, "method": _S, "kwargs": {"type": "object"}}, ["service", "method"])),
    "run_sql": (_t_run_sql, _fn(
        "run_sql", "Run one read-only SQL statement on a SQL warehouse of the selected target (SELECT, SHOW, "
        "DESCRIBE; system tables like system.access.audit work). Returns up to 50 rows.",
        {"statement": _S, "warehouse_id": _S, "catalog": _S, "schema": _S}, ["statement"])),
    "read_docs": (_t_read_docs, _fn(
        "read_docs", "Fetch a Databricks documentation page as text. Allowed hosts: " + ", ".join(DOC_HOSTS)
        + ". Useful: https://docs.databricks.com/api/workspace/<service>/<method> and "
        "https://databricks-sdk-py.readthedocs.io/en/latest/workspace/<area>/<service>.html",
        {"url": _S}, ["url"])),
    "validate_script": (_t_validate, _fn(
        "validate_script", "Statically check a script without running it: syntax, unknown SDK calls, "
        "blocklisted calls, declared checks, each call's risk tier. Defaults to the editor buffer.",
        {"script": _S})),
    "dry_run": (_t_dry_run, _fn(
        "dry_run", "Run a script once against the selected target at QUICK depth (reads only; mutating calls "
        "are skipped) and get every check's outcome. Defaults to your latest proposal, else the buffer. "
        "Nothing is recorded.", {"script": _S})),
    "propose_script": (_t_propose, _fn(
        "propose_script", "Propose the COMPLETE new script for the editor. The human reviews a diff and "
        "applies it. Returns validation; if it isn't ok, fix and propose again.",
        {"script": _S, "summary": {"type": "string", "description": "one or two sentences: what changed"}},
        ["script", "summary"])),
    "get_test_script": (_t_get_test_script, _fn(
        "get_test_script", "Read another Test's script (as an example or to reuse its checks).",
        {"test_id": _S}, ["test_id"])),
    "search_tests": (_t_search_tests, _fn(
        "search_tests", "Search existing Tests by name, description, tags, check names or script code "
        "(e.g. an SDK method like 'grants.get'). Best matches first.", {"query": _S, "limit": {"type": "integer"}})),
    "search_targets": (_t_targets, _fn("search_targets", "List registered targets (workspaces).", {"query": _S})),
    "search_roles": (_t_roles, _fn(
        "search_roles", "Roles registered on a target (usable as check(..., role=...)). Defaults to the selected target.",
        {"target_id": _S})),
    "recent_results": (_t_recent_results, _fn(
        "recent_results", "Recent recorded results of this Test's checks (on the selected target unless all_targets).",
        {"test_id": _S, "all_targets": {"type": "boolean"}})),
    "failure_archive": (_t_failure_archive, _fn(
        "failure_archive", "Past failures across all runs by failure bucket and/or stage.",
        {"bucket": _S, "stage": _S, "limit": {"type": "integer"}})),
}


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
SCRIPT_GUIDE = '''A Test is a Python script. These names are pre-bound:

  w                Databricks SDK WorkspaceClient (keyword args only), runs as the target's identity.
                   Results are plain dicts with attribute access: cluster.cluster_id == cluster["cluster_id"].
                   Long-running ops return the final object (no .result() needed).
  w.api.get(path, query=None) / post / put / patch / delete   raw REST, path starts with /api/
  check(name, *, role=None, advisory=False, description="")
                   `with check("..."):` is one result. Passes when the block finishes; fails on `assert` or an API
                   error. A failed check doesn't stop the script. Checks can't nest. Names must be unique literals.
  expect_deny(name, *, at="AUTHORIZE", role=None)
                   counterfactual: passes only if something in the block is denied (AUTHORIZE = permission
                   denied, CONNECT = network denial, OPERATE = any API error). Succeeding is a failure.
  sql(statement, *, warehouse_id=None, catalog=None, schema=None) -> list of row dicts
  http(url, *, method="GET", headers=None, body=None, timeout=15) -> {status_code, headers, text}  (egress probe)
  record(key, value)   attach evidence to the current check      log(*parts)   a run-log line
  target           {name, workspace_name, host, safety_class, tags, roles}
  depth            "quick" (reads only; mutating calls raise DepthSkipped → the check is SKIPPED) or "full"
  params           per-run parameters
  Exceptions: ApiError, PermissionDenied, NotFound, ConnectionFailed, SafetyBlocked, DepthSkipped

Safety: every call is gated by the target's safety class (read-only → reads only; sandbox → up to deletes;
prod-for-us → creates/updates). Blocklisted calls (deleting workspaces/metastores/catalogs, purges) can never run.
A check with a blocked call records BLOCKED_BY_SAFETY. Code outside checks runs too but records nothing.
Direct sockets are refused: use w, sql() and http().

Style: one behaviour per check, named as the claim it proves ("analysts can't read the payroll table").
Assert on the specific thing with a helpful message. record() the facts a reader needs to understand a failure.
Clean up anything a check creates in try/finally. Prefer reads; when a check must mutate, keep it to a
throwaway resource with a recognisable name prefix like "vp-test-".'''

SYSTEM = f'''You are the test-editor assistant for a Databricks validation platform. You help a human write
and fix ONE Test, a Python script, shown in the editor beside you.

{SCRIPT_GUIDE}

How to work:
- Ground every SDK call: search_sdk / describe_sdk before using a method you haven't verified. Never invent
  methods or parameters. When response shapes matter, look at real data with sdk_call or run_sql.
- To change the script, call propose_script with the COMPLETE script (never a fragment). Keep existing check
  names unless asked to rename them — names carry run history. Preserve the human's unrelated code.
- After proposing, dry_run it when a target is selected, then fix what fails (a failure that is the real
  finding — e.g. a permission genuinely missing — is not a bug in the test; say so).
- Be brief in your final reply: what you changed or found, and anything the human must decide. No code in the
  reply text when you've proposed it (the human sees the diff). Answer questions directly when no edit is needed.
- You can't save, activate, schedule or run the test for real; the human does that.'''


def _context_block(st: EditorState) -> str:
    parts = []
    if st.test is not None:
        parts.append(f"Test: {st.test.name} (id {st.test.id}, status {st.test.status.value}, "
                     f"default role {st.test.run_as_role or 'target identity'})")
    else:
        parts.append("Test: not saved yet")
    if st.target is not None:
        roles = [r.get("label") or r.get("group_id") for r in (st.target.assumable_roles or [])]
        parts.append(f"Selected target: {st.target.name} (id {st.target.id}, safety class "
                     f"{st.target.safety_class.value}, workspace {st.target.workspace_name or '?'}, "
                     f"roles {roles or 'none'}, tags {st.target.tags or {}})")
    else:
        parts.append("Selected target: none (dry_run, sdk_call and run_sql need one)")
    parts.append("Current editor buffer:\n```python\n" + (st.buffer or "") + "\n```")
    return "\n".join(parts)


def _dry_run_block(last: Optional[dict]) -> str:
    if not last:
        return ""
    return "\nThe human's last dry run of this buffer:\n" + _clip(_condense_dry_run(last), 4000)


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------
def _default_model(messages: list[dict], tools: list[dict]) -> ModelReply:
    from app.clients.model_serving import chat_with_tools
    from app.core.config import settings

    return chat_with_tools(messages, tools=tools, tool_choice="auto",
                           max_tokens=getattr(settings, "ASSISTANT_MAX_TOKENS", 6000), temperature=0.1)


def _assistant_message(reply: ModelReply) -> dict:
    return {"role": "assistant", "content": reply.content or "",
            "tool_calls": [{"id": tc.id or f"call_{i}", "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                           for i, tc in enumerate(reply.tool_calls)]}


def _summarize_args(name: str, args: dict) -> str:
    if name in ("propose_script", "validate_script", "dry_run"):
        return str(args.get("summary") or "")[:160]
    return ", ".join(f"{k}={str(v)[:60]}" for k, v in args.items() if k != "script")[:200]


def editor_turn(session: Session, *, messages: list[dict], buffer: str, test_id: Optional[str] = None,
                target_id: Optional[str] = None, last_dry_run: Optional[dict] = None,
                model_call: Optional[EditorModel] = None) -> dict:
    """One assistant turn in the editor. Returns the reply and (maybe) a proposed script."""
    st = EditorState(session=session, buffer=buffer or "",
                     target=session.get(Target, target_id) if target_id else None,
                     test=session.get(Test, test_id) if test_id else None)
    model = model_call or _default_model
    history = [{"role": m.get("role"), "content": str(m.get("content") or "")}
               for m in messages[-MAX_HISTORY:] if m.get("role") in ("user", "assistant")]
    wire = [{"role": "system", "content": SYSTEM + "\n\n" + _context_block(st) + _dry_run_block(last_dry_run)},
            *history]
    schemas = [schema for _, schema in TOOLS.values()]
    reply_text = ""
    for _ in range(MAX_ITERS):
        try:
            reply = model(wire, schemas)
        except ModelServingError as exc:
            return {"reply": f"The assistant model isn't reachable: {exc}", "proposal": st.proposal,
                    "steps": st.steps, "dry_run": st.dry_run, "error": True}
        if not reply.tool_calls:
            reply_text = reply.content or ""
            break
        wire.append(_assistant_message(reply))
        for i, tc in enumerate(reply.tool_calls):
            fn = TOOLS.get(tc.name, (None, None))[0]
            try:
                result = fn(st, tc.arguments or {}) if fn else {"error": f"unknown tool {tc.name}"}
            except Exception as exc:  # noqa: BLE001 - a tool failure is information for the model
                logger.warning("editor tool %s failed: %s", tc.name, exc, exc_info=True)
                result = {"error": f"{type(exc).__name__}: {exc}"}
            ok = not (isinstance(result, dict) and result.get("error"))
            st.steps.append({"tool": tc.name, "detail": _summarize_args(tc.name, tc.arguments or {}), "ok": ok})
            wire.append({"role": "tool", "tool_call_id": tc.id or f"call_{i}", "name": tc.name,
                         "content": _clip(result)})
    else:
        reply_text = ("I ran out of steps for this turn. " +
                      ("My latest proposal is ready to review." if st.proposal else "Ask me to continue."))
    return {"reply": reply_text.strip(), "proposal": st.proposal, "steps": st.steps, "dry_run": st.dry_run}


def model_available() -> bool:
    from app.core.config import settings

    return bool(settings.MODEL_SERVING_ENDPOINT)
