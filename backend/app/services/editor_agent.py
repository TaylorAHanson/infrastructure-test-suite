"""The test-editor agent: a multi-turn coding assistant for one Test script.

It works beside the human in the editor. Each turn it sees the conversation, the
current editor buffer, the selected target and the last dry-run result, and can:

* look things up — the Databricks SDK surface and docstrings, the docs sites,
  other Tests' scripts, run history and the failure archive;
* look at the real workspace — read-only SDK calls and read-only SQL, as the
  target's identity, through the same safety gate as a run;
* check its work — static validation and a quick (read-only) dry run;
* change the script with :func:`edit_script` (exact replacements) or
  :func:`propose_script` (a whole new script). Either way the result is a
  proposal, never applied: the human reviews the diff and keeps or undoes it.

There is no tool that saves, activates, deletes or runs anything for real.
The model is reached through the injectable ``model_call`` seam so tests stub it.
Progress is reported through ``emit`` as it happens, for the streaming endpoint.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
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
from app.services import assistant_config, assistant_tools, script_tests

logger = logging.getLogger(__name__)

EditorModel = Callable[[list[dict], list[dict]], ModelReply]
Emit = Callable[[dict], None]

MAX_TOOL_CHARS = 9000
MAX_HISTORY = 24
DOC_HOSTS = ("docs.databricks.com", "learn.microsoft.com", "docs.gcp.databricks.com",
             "databricks-sdk-py.readthedocs.io", "api-docs.databricks.com", "kb.databricks.com")
LIVE_READ_TOOLS = ("sdk_call", "run_sql")


@dataclass
class EditorState:
    session: Session
    target: Optional[Target]
    buffer: str
    test: Optional[Test]
    proposal: Optional[dict] = None
    dry_run: Optional[dict] = None
    steps: list[dict] = field(default_factory=list)
    emit: Emit = field(default=lambda event: None)

    @property
    def working(self) -> str:
        """The script as the assistant has left it so far: its proposal, else the buffer."""
        return (self.proposal or {}).get("script") or self.buffer


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


def _record_proposal(st: EditorState, script: str, summary: str) -> dict:
    analysis = analyze(script)
    st.proposal = {"script": script, "summary": summary, "analysis": analysis}
    st.emit({"type": "proposal", "proposal": st.proposal})
    return {"recorded": True, "ok": analysis["ok"], "errors": analysis["errors"],
            "warnings": analysis["warnings"], "checks": [c["name"] for c in analysis["checks"]],
            "max_risk_tier": analysis["max_risk_tier"],
            "note": ("The human will see this as a diff to review." if analysis["ok"] else
                     "This can't be saved yet — fix the errors with another edit.")}


def _t_propose(st: EditorState, args: dict) -> Any:
    script = str(args.get("script") or "")
    if not script.strip():
        return {"error": "propose_script needs the complete script"}
    return _record_proposal(st, script, str(args.get("summary") or ""))


def _numbered(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + i:>4}| {line}" for i, line in enumerate(lines))


def _t_edit(st: EditorState, args: dict) -> Any:
    edits = args.get("edits")
    if isinstance(edits, str):
        try:
            edits = json.loads(edits)
        except ValueError:
            return {"error": "edits must be a list of {old, new} objects"}
    if not isinstance(edits, list) or not edits:
        return {"error": "edits must be a non-empty list of {old, new} objects"}
    script = st.working
    spans: list[tuple[int, int]] = []
    for n, edit in enumerate(edits, 1):
        if not isinstance(edit, dict):
            return {"error": f"edit {n} must be an object with old and new"}
        old, new = str(edit.get("old") or ""), str(edit.get("new") or "")
        if not old:
            if script.strip():
                return {"error": f"edit {n}: old is empty. To add lines, put the lines they go after in old "
                                 "and repeat them at the start of new."}
            script = new
            spans.append((0, len(new)))
            continue
        count = script.count(old)
        if count != 1:
            hint = ("not found: old must match the current script exactly, indentation included "
                    "(view_script shows it)") if count == 0 else (
                f"matches {count} places: include more surrounding lines so it's unique")
            return {"error": f"edit {n} {hint}. No edits were applied."}
        at = script.index(old)
        script = script[:at] + new + script[at + len(old):]
        shift = len(new) - len(old)
        spans = [(s + shift, e + shift) if s >= at + len(old) else (s, e) for s, e in spans]
        spans.append((at, at + len(new)))
    if script == st.working:
        return {"error": "these edits don't change the script"}
    result = _record_proposal(st, script, str(args.get("summary") or ""))
    lines = script.split("\n")
    regions = []
    for start, end in spans[-4:]:
        first = script.count("\n", 0, start)
        last = script.count("\n", 0, end)
        lo, hi = max(0, first - 2), min(len(lines), last + 3)
        regions.append(_numbered(lines[lo:hi], lo + 1))
    return {**result, "edited": "\n…\n".join(regions)}


def _t_view(st: EditorState, args: dict) -> Any:
    lines = st.working.split("\n")
    start = max(1, int(args.get("start_line") or 1))
    end = min(len(lines), int(args.get("end_line") or len(lines)))
    return {"of": "your latest edit" if st.proposal else "the editor buffer", "lines": len(lines),
            "text": _numbered(lines[start - 1:end], start)}


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
    from app.services.run_service import _demo_mode

    if _demo_mode():
        return None
    return script_tests.client_factory(script_tests._target_view(st.target))(role)


def _gate_read(st: EditorState, op) -> Optional[str]:
    from app.services.safety import evaluate_operation_safety

    # Never fall back to the app's own identity: it can read every target's stored credentials.
    if st.target is None:
        return "select a target in the editor first: the assistant reads live data only as a target's identity"
    decision = evaluate_operation_safety(op, st.target.safety_class, request_source=RequestSource.API,
                                         target_name=st.target.name)
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
        if not wid:
            wid = (st.target.tags or {}).get("warehouse_id")
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
    return {"test_id": test.id, "name": test.name, "script": script_tests.script_for(test)}


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
    "edit_script": (_t_edit, _fn(
        "edit_script", "Change the script with exact find-and-replace edits, applied in order to your latest "
        "edit (or the editor buffer). Each old must appear exactly once, character for character with its "
        "indentation; include a few surrounding lines to make it unique. To delete, use an empty new. The "
        "human reviews the result as a diff. Returns validation and the edited lines; if it isn't ok, edit again.",
        {"edits": {"type": "array", "items": {"type": "object", "properties": {"old": _S, "new": _S},
                                              "required": ["old", "new"]}},
         "summary": {"type": "string", "description": "one or two sentences: what changed and why"}},
        ["edits", "summary"])),
    "view_script": (_t_view, _fn(
        "view_script", "Read the script as it stands after your edits, with line numbers (all of it, or a "
        "line range).", {"start_line": {"type": "integer"}, "end_line": {"type": "integer"}})),
    "propose_script": (_t_propose, _fn(
        "propose_script", "Replace the whole script. Only for a new test or a rewrite of most of it; for "
        "anything smaller use edit_script. The human reviews a diff. Returns validation.",
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
- To change the script, use edit_script with small exact replacements; it's much faster than rewriting.
  Use propose_script only for a brand-new test or when most of the script changes. Keep existing check
  names unless asked to rename them — names carry run history. Preserve the human's unrelated code.
- After editing, dry_run it when a target is selected, then fix what fails (a failure that is the real
  finding — e.g. a permission genuinely missing — is not a bug in the test; say so).
- Before a batch of tool calls, say in one short sentence what you're about to check; the human sees it live.
- Be brief in your final reply: what you changed or found, and anything the human must decide. No code in the
  reply text when you've edited (the human sees the diff). Answer questions directly when no edit is needed.
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
def _configured_model(cfg: assistant_config.AssistantSettings) -> EditorModel:
    from app.clients.model_serving import chat_with_tools

    def call(messages: list[dict], tools: list[dict]) -> ModelReply:
        return chat_with_tools(messages, tools=tools, tool_choice="auto", endpoint=cfg.model,
                               max_tokens=cfg.max_output_tokens, temperature=cfg.temperature,
                               reasoning=cfg.reasoning)

    return call


def _assistant_message(reply: ModelReply) -> dict:
    return {"role": "assistant", "content": reply.content or "",
            "tool_calls": [{"id": tc.id or f"call_{i}", "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                           for i, tc in enumerate(reply.tool_calls)]}


def _short(value: Any, limit: int = 90) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _edit_label(a: dict) -> str:
    n = len(a["edits"]) if isinstance(a.get("edits"), list) else 1
    return f"Editing ({n} {'change' if n == 1 else 'changes'}): {_short(a.get('summary', ''), 100)}"


def _view_label(a: dict) -> str:
    lo, hi = a.get("start_line"), a.get("end_line")
    return f"Reading lines {lo or 1}–{hi or 'end'} of the script" if lo or hi else "Reading the script"


def _call_label(a: dict) -> str:
    kw = ", ".join(f"{k}={_short(v, 30)}" for k, v in (a.get("kwargs") or {}).items())
    return f"Calling w.{a.get('service', '')}.{a.get('method', '')}({_short(kw, 60)}) on the target"


_LABELS: dict[str, Callable[[dict], str]] = {
    "search_sdk": lambda a: f"Searching the SDK for “{_short(a.get('query', ''), 60)}”",
    "describe_sdk": lambda a: (f"Reading the docs for w.{a.get('service', '')}.{a['method']}" if a.get("method")
                               else f"Listing w.{a.get('service', '')} methods"),
    "sdk_call": _call_label,
    "run_sql": lambda a: f"Running SQL: {_short(a.get('statement', ''), 100)}",
    "read_docs": lambda a: f"Reading {_short(a.get('url', ''), 100)}",
    "validate_script": lambda a: "Validating the script",
    "dry_run": lambda a: "Dry-running on the target (quick: reads only)",
    "edit_script": _edit_label,
    "view_script": _view_label,
    "propose_script": lambda a: f"Writing the whole script: {_short(a.get('summary', ''), 100)}",
    "get_test_script": lambda a: f"Reading test {a.get('test_id', '')}",
    "search_tests": lambda a: f"Searching tests for “{_short(a.get('query', ''), 60)}”",
    "search_targets": lambda a: "Listing targets",
    "search_roles": lambda a: "Listing the target's roles",
    "recent_results": lambda a: "Reading this test's recent results",
    "failure_archive": lambda a: "Reading past failures",
}


def _describe(name: str, args: dict) -> str:
    """What a tool call is doing, in words for the human watching."""
    label = _LABELS.get(name)
    return label(args or {}) if label else name


def _outcome(name: str, result: Any) -> str:
    """A few words on what a tool call returned."""
    if isinstance(result, dict) and result.get("error"):
        err = result["error"]
        return "failed: " + _short(err.get("message") if isinstance(err, dict) else err, 140)
    if isinstance(result, dict) and result.get("demo"):
        return "demo mode: no live data"
    if name in ("edit_script", "propose_script", "validate_script") and isinstance(result, dict):
        errors = result.get("errors") or []
        checks = result.get("checks") or []
        return (f"{len(errors)} {'error' if len(errors) == 1 else 'errors'} to fix" if errors
                else f"valid · {len(checks)} {'check' if len(checks) == 1 else 'checks'}")
    if name == "dry_run" and isinstance(result, dict):
        counts: dict[str, int] = {}
        for c in result.get("checks") or []:
            counts[c.get("outcome", "?")] = counts.get(c.get("outcome", "?"), 0) + 1
        return " · ".join(f"{v} {k}" for k, v in counts.items()) or "no checks ran"
    if name == "run_sql" and isinstance(result, dict):
        return f"{result.get('row_count', 0)} rows"
    if name == "sdk_call" and isinstance(result, dict) and "count" in result:
        return f"{result['count']} items"
    if name == "read_docs" and isinstance(result, dict):
        return f"{len(result.get('text') or '') // 1000}k chars"
    if isinstance(result, list):
        return f"{len(result)} {'result' if len(result) == 1 else 'results'}"
    return "done"


WRAP_UP = ("You've used this turn's tool-call budget. Don't call any more tools: reply to the human now with "
           "what you did, what you found, and what's left to do.")


def editor_turn(session: Session, *, messages: list[dict], buffer: str, test_id: Optional[str] = None,
                target_id: Optional[str] = None, last_dry_run: Optional[dict] = None,
                model_call: Optional[EditorModel] = None, emit: Optional[Emit] = None,
                cancel: Optional[threading.Event] = None) -> dict:
    """One assistant turn in the editor. Returns the reply and (maybe) a proposed script.

    ``emit`` receives progress events as they happen (thinking, the model's
    interim text and reasoning, each tool call starting and finishing)."""
    cfg = assistant_config.effective(session)
    st = EditorState(session=session, buffer=buffer or "",
                     target=session.get(Target, target_id) if target_id else None,
                     test=session.get(Test, test_id) if test_id else None,
                     emit=emit or (lambda event: None))
    model = model_call or _configured_model(cfg)
    disabled = ([*LIVE_READ_TOOLS] if not cfg.allow_live_reads else []) + (
        ["dry_run"] if not cfg.allow_dry_run else [])
    history = [{"role": m.get("role"), "content": str(m.get("content") or "")}
               for m in messages[-MAX_HISTORY:] if m.get("role") in ("user", "assistant")]
    system = SYSTEM + "\n\n" + _context_block(st) + _dry_run_block(last_dry_run)
    if disabled:
        system += f"\nTurned off by the admin for this deployment: {', '.join(disabled)}."
    wire = [{"role": "system", "content": system}, *history]
    schemas = [schema for name, (_, schema) in TOOLS.items() if name not in disabled]
    started = time.monotonic()
    calls = 0
    noted: set[str] = set()

    def finish(reply_text: str, **extra: Any) -> dict:
        return {"reply": reply_text.strip(), "proposal": st.proposal, "steps": st.steps,
                "dry_run": st.dry_run, **extra}

    def ask(round_no: int) -> ModelReply:
        st.emit({"type": "thinking", "round": round_no})
        reply = model(wire, schemas)
        for note in getattr(reply, "notes", None) or []:
            if note not in noted:
                noted.add(note)
                st.emit({"type": "notice", "text": note})
        if getattr(reply, "reasoning", None):
            st.emit({"type": "reasoning", "text": reply.reasoning})
        return reply

    for round_no in range(1, cfg.max_tool_calls + 2):
        if cancel is not None and cancel.is_set():
            return finish("Stopped." + (" My latest edit is ready to review." if st.proposal else ""),
                          stopped=True)
        if time.monotonic() - started > cfg.turn_timeout_seconds:
            return finish(f"I hit the {cfg.turn_timeout_seconds}s limit for one turn. " +
                          ("My latest edit is ready to review." if st.proposal else "Ask me to continue."))
        try:
            reply = ask(round_no)
        except ModelServingError as exc:
            return finish(f"The assistant model isn't reachable: {exc}", error=True)
        if not reply.tool_calls:
            return finish(reply.content or "")
        if reply.content and reply.content.strip():
            st.emit({"type": "message", "text": reply.content.strip()})
        wire.append(_assistant_message(reply))
        exhausted = False
        for i, tc in enumerate(reply.tool_calls):
            call_id = tc.id or f"call_{i}"
            if calls >= cfg.max_tool_calls or (cancel is not None and cancel.is_set()):
                exhausted = True
                wire.append({"role": "tool", "tool_call_id": call_id, "name": tc.name,
                             "content": json.dumps({"error": "not run: this turn's tool-call budget is used up"})})
                continue
            calls += 1
            args = tc.arguments or {}
            detail = _describe(tc.name, args)
            step_no = len(st.steps)
            st.emit({"type": "tool_start", "step": step_no, "tool": tc.name, "detail": detail})
            t0 = time.monotonic()
            fn = TOOLS.get(tc.name, (None, None))[0] if tc.name not in disabled else None
            try:
                result = fn(st, args) if fn else {"error": f"unknown tool {tc.name}"}
            except Exception as exc:  # noqa: BLE001 - a tool failure is information for the model
                logger.warning("editor tool %s failed: %s", tc.name, exc, exc_info=True)
                result = {"error": f"{type(exc).__name__}: {exc}"}
            ok = not (isinstance(result, dict) and result.get("error"))
            step = {"tool": tc.name, "detail": detail, "ok": ok, "result": _outcome(tc.name, result),
                    "ms": int((time.monotonic() - t0) * 1000)}
            st.steps.append(step)
            st.emit({"type": "tool_end", "step": step_no, **step})
            wire.append({"role": "tool", "tool_call_id": call_id, "name": tc.name, "content": _clip(result)})
        if exhausted or calls >= cfg.max_tool_calls:
            break

    if cancel is not None and cancel.is_set():
        return finish("Stopped." + (" My latest edit is ready to review." if st.proposal else ""), stopped=True)
    st.emit({"type": "notice", "text": f"Reached the limit of {cfg.max_tool_calls} tool calls for one turn."})
    wire.append({"role": "user", "content": WRAP_UP})
    try:
        reply = ask(cfg.max_tool_calls + 2)
        text = reply.content or ""
    except ModelServingError:
        text = ""
    return finish(text or (f"I used all {cfg.max_tool_calls} tool calls for this turn. " +
                           ("My latest edit is ready to review." if st.proposal else "Ask me to continue.")))


def model_available() -> bool:
    return bool(assistant_config.effective().model)
