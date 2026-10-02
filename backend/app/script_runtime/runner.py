"""Run a Test script in an isolated subprocess, brokering every Databricks call.

The sandbox (``sandbox/bootstrap.py``) starts with a scrubbed environment: no
app credentials, no Lakebase connection details, no target token, a throwaway
``HOME``. It reaches Databricks only by asking this process, one JSON line per
call. For each call the broker:

1. resolves the call to a real SDK method (a script can't name ``config`` or
   ``api_client``, so credentials are unreachable);
2. checks the check's ``role`` is registered on the target and within its class;
3. runs the platform safety gate on it (blocklist floor, risk tier vs the
   target's safety class, the human-only override) — the same
   :func:`app.services.safety.evaluate_operation_safety` the structured checks use;
4. skips anything mutating in a ``quick`` run;
5. makes the call as the target's identity and returns plain JSON.

A check that had any call blocked is recorded ``BLOCKED_BY_SAFETY`` even if the
script swallowed the exception, and one that hit a depth skip is ``SKIPPED``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.enums import Depth, RequestSource, RiskTier
from app.core.operations import Operation
from app.script_runtime import sdk_catalog

logger = logging.getLogger(__name__)

SANDBOX_DIR = Path(__file__).resolve().parent / "sandbox"
BOOTSTRAP = SANDBOX_DIR / "bootstrap.py"

_SQL_STRIP = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`", re.S)
# Statements that only read (USE/SET/RESET/DECLARE change session state, nothing stored).
_SQL_READ_LEADS = {"select", "show", "describe", "desc", "explain", "values", "list", "table", "from",
                   "use", "set", "reset", "declare"}
_SQL_DYNAMIC = re.compile(r"\bexecute\s+immediate\b")
_SQL_CATASTROPHIC = re.compile(r"\bdrop\s+(catalog|schema|database)\b")
# A keyword followed by "(" is a function call (replace(...)), not a statement.
# CREATE OR REPLACE stays a write: it's the idempotent way to (re)create your own
# table, and Delta keeps the replaced version's history.
_SQL_DESTRUCTIVE = re.compile(r"\b(drop|truncate|delete|vacuum|overwrite)\b(?!\s*\()|\breplace\s+where\b")
_SQL_DML = re.compile(r"\b(insert|update|merge|delete|drop|truncate|overwrite)\b(?!\s*\()")

# The last segment of a raw REST path that makes a POST/PUT/PATCH destructive.
_RAW_DESTRUCTIVE_ACTION = re.compile(r"(^|_)(delete|remove|destroy|purge|wipe|drop|uninstall|terminate)(_|$)")
_RAW_STATEMENTS_PATH = re.compile(r"^/api/2\.\d+/sql/statements/?$")


@dataclass
class RunContext:
    """Everything the broker needs to gate and perform one script run."""

    target: Any = None  # app.models.Target | None
    depth: Depth = Depth.QUICK
    request_source: RequestSource = RequestSource.API
    override: Any = None  # app.services.safety.SafetyOverride | None
    demo_mode: bool = False
    #: ``role -> WorkspaceClient`` bound to (target, role?). Raises on identity failure.
    client_for: Optional[Callable[[Optional[str]], Any]] = None
    params: dict = field(default_factory=dict)
    #: Role used by checks that don't name one (the Test's ``run_as_role``).
    default_role: Optional[str] = None
    timeout_seconds: int = 300
    memory_mb: int = 1024


@dataclass
class CheckResult:
    name: str
    kind: str = "assertion"
    role: Optional[str] = None
    advisory: bool = False
    description: str = ""
    expected_stage: Optional[str] = None
    outcome: str = "not_reached"  # pass | fail | error | blocked | skipped | not_reached
    stage: Optional[str] = None
    bucket: Optional[str] = None
    message: str = ""
    traceback: Optional[str] = None
    line: Optional[int] = None
    evidence: dict = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    ms: Optional[int] = None

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "name", "kind", "role", "advisory", "description", "expected_stage", "outcome",
            "stage", "bucket", "message", "traceback", "line", "evidence", "calls", "ms")}


@dataclass
class ScriptResult:
    checks: list[CheckResult] = field(default_factory=list)
    error: Optional[dict] = None
    output: str = ""
    logs: list[str] = field(default_factory=list)
    setup_calls: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    timed_out: bool = False

    def check(self, name: str) -> Optional[CheckResult]:
        return next((c for c in self.checks if c.name == name), None)

    def to_dict(self) -> dict:
        return {"checks": [c.to_dict() for c in self.checks], "error": self.error,
                "output": self.output, "logs": self.logs, "setup_calls": self.setup_calls,
                "duration_ms": self.duration_ms, "timed_out": self.timed_out}


# ---------------------------------------------------------------------------
# Classifying a call
# ---------------------------------------------------------------------------
_SQL_SEVERITY = ["GET", "POST", "DELETE", "DROP_CATALOG", "DYNAMIC"]


def _statement_class(stmt: str) -> str:
    if _SQL_DYNAMIC.search(stmt):
        return "DYNAMIC"
    if _SQL_CATASTROPHIC.search(stmt):
        return "DROP_CATALOG"
    first = re.match(r"[\s(]*(\w+)", stmt)
    lead = first.group(1) if first else ""
    if lead in _SQL_READ_LEADS:
        return "GET"
    if lead == "with" and not _SQL_DML.search(stmt):
        return "GET"
    if _SQL_DESTRUCTIVE.search(stmt):
        return "DELETE"
    return "POST"


def _sql_class(statement: str) -> str:
    """The most dangerous class across every statement in ``statement``.

    Comments and quoted text are removed first, so a keyword inside a string or
    comment doesn't count and ``SELECT 1; DROP TABLE t`` is judged by its DROP.
    """
    text = _SQL_STRIP.sub(" ", (statement or "").lower())
    classes = [_statement_class(s) for s in text.split(";") if s.strip()]
    return max(classes, key=_SQL_SEVERITY.index) if classes else "POST"


def sql_verb(statement: str) -> str:
    """The coarse verb a SQL statement deserves: reads GET (R0), writes POST, drops DELETE."""
    cls = _sql_class(statement)
    return "DELETE" if cls in ("DROP_CATALOG", "DYNAMIC") else cls


def effective_verb(service: str, method: str, kwargs: dict) -> str:
    if service == "statement_execution" and method == "execute_statement":
        return sql_verb(str(kwargs.get("statement", "")))
    return sdk_catalog.verb_for(service, method)


def _sql_operation(statement: str) -> Operation:
    """``DROP CATALOG`` and ``EXECUTE IMMEDIATE`` map onto blocklisted (R3) operations."""
    cls = _sql_class(statement)
    if cls == "DROP_CATALOG":
        return Operation(service="statement_execution", method="drop_catalog", verb="DELETE")
    if cls == "DYNAMIC":
        return Operation(service="statement_execution", method="execute_immediate", verb="DELETE")
    return Operation(service="statement_execution", method="execute_statement", verb=cls)


def raw_api_operation(verb: str, path: str, body: Any = None) -> Operation:
    """Classify a ``w.api`` call the way the equivalent SDK call would be.

    A statement POSTed to ``/api/2.x/sql/statements`` is judged by its SQL, a POST
    to an action like ``clusters/permanent-delete`` is destructive, and reading a
    secret's value (``secrets/get``) maps onto the blocklisted ``get_secret``.
    """
    clean = path.split("?", 1)[0].lower()
    verb = verb.upper()
    if verb == "POST" and _RAW_STATEMENTS_PATH.match(clean):
        statement = body.get("statement") if isinstance(body, dict) else None
        return _sql_operation(str(statement or ""))
    norm = clean.replace("-", "_")
    segments = [p for p in norm.split("/") if p]
    last = segments[-1] if segments else ""
    if verb in ("POST", "PUT", "PATCH") and _RAW_DESTRUCTIVE_ACTION.search(last):
        verb = "DELETE"
    method = f"{verb.lower()}_{last}"
    if "secrets" in segments and last == "get":
        method = "get_secret"
    return Operation(service=norm, method=method, verb=verb)


# ---------------------------------------------------------------------------
# The broker
# ---------------------------------------------------------------------------
class _Broker:
    def __init__(self, ctx: RunContext, result: ScriptResult):
        self.ctx = ctx
        self.result = result
        self.blocked_in: set[str] = set()
        self.depth_skipped_in: set[str] = set()
        self.duplicates: set[str] = set()
        self._clients: dict[Optional[str], Any] = {}
        self._demo: Any = None

    # -- recording ------------------------------------------------------
    def _record(self, check: Optional[str], entry: dict) -> None:
        if check:
            res = self.result.check(check)
            if res is not None:
                res.calls.append(entry)
                return
        self.result.setup_calls.append(entry)

    # -- identity -------------------------------------------------------
    def _role_problem(self, role: Optional[str]) -> Optional[str]:
        if not role:
            return None
        from app.services.roles import find_role, role_within_class

        target = self.ctx.target
        if target is None:
            return "a role needs a registered target"
        found = find_role(target.assumable_roles, role)
        if found is None:
            return f"role '{role}' is not registered on target '{target.name}'"
        if not role_within_class(found, target.safety_class):
            return f"role '{role}' out-runs target class '{target.safety_class.value}'"
        return None

    def _client(self, role: Optional[str]):
        if role not in self._clients:
            if self.ctx.client_for is None:
                raise RuntimeError("no Databricks client available")
            self._clients[role] = self.ctx.client_for(role)
        return self._clients[role]

    # -- gate -----------------------------------------------------------
    def _gate(self, op: Operation):
        from app.services.safety import evaluate_operation_safety

        target = self.ctx.target
        return evaluate_operation_safety(
            op, target.safety_class if target is not None else None,
            override=self.ctx.override, request_source=self.ctx.request_source,
            target_name=target.name if target is not None else None)

    def handle(self, msg: dict) -> dict:
        check = msg.get("check")
        role = msg.get("role") or self.ctx.default_role
        kind = msg.get("type")
        t0 = time.monotonic()

        if kind == "call":
            service, method = str(msg.get("service", "")), str(msg.get("method", ""))
            kwargs = msg.get("kwargs") or {}
            label = f"{service}.{method}"
            meta = sdk_catalog.get_method(service, method)
            if meta is None:
                err = {"kind": "invalid", "message": f"w.{label} is not a Databricks SDK method"
                       + ("" if service in sdk_catalog.catalog() else f" ('{service}' is not a service)")}
                self._record(check, {"op": label, "outcome": "invalid", "message": err["message"]})
                return {"ok": False, "error": err}
            if service == "statement_execution" and method == "execute_statement":
                op = _sql_operation(str(kwargs.get("statement", "")))
            else:
                op = Operation(service=service, method=method, verb=effective_verb(service, method, kwargs))
        elif kind == "api":
            verb = str(msg.get("verb", "GET")).upper()
            path = str(msg.get("path", ""))
            label = f"{verb} {path}"
            if not path.startswith("/api/"):
                err = {"kind": "invalid", "message": "w.api paths must start with /api/"}
                self._record(check, {"op": label, "outcome": "invalid", "message": err["message"]})
                return {"ok": False, "error": err}
            op = raw_api_operation(verb, path, msg.get("body"))
        elif kind == "http":
            method = str(msg.get("method", "GET")).upper()
            url = str(msg.get("url", ""))
            label = f"{method} {url}"
            if not url.startswith(("http://", "https://")):
                err = {"kind": "invalid", "message": "http() needs an http:// or https:// URL"}
                self._record(check, {"op": label, "outcome": "invalid", "message": err["message"]})
                return {"ok": False, "error": err}
            op = Operation(service="http", method=method.lower(),
                           verb="GET" if method in ("GET", "HEAD", "OPTIONS") else method)
        else:
            return {"ok": False, "error": {"kind": "invalid", "message": f"unknown request {kind!r}"}}

        entry: dict = {"op": label, "verb": op.verb}
        if msg.get("line"):
            entry["line"] = msg["line"]
        if role:
            entry["role"] = role
        problem = self._role_problem(role)
        if problem:
            entry.update(outcome="blocked", message=problem, tier=None)
            self._record(check, entry)
            if check:
                self.blocked_in.add(check)
            return {"ok": False, "error": {"kind": "blocked", "message": problem}}

        decision = self._gate(op)
        entry["tier"] = decision.effective_tier.value
        if not decision.allowed:
            entry.update(outcome="blocked", message=decision.reason)
            self._record(check, entry)
            if check:
                self.blocked_in.add(check)
            return {"ok": False, "error": {"kind": "blocked", "message": decision.reason}}
        if decision.overridden:
            entry["overridden"] = True
        if self.ctx.depth == Depth.QUICK and decision.effective_tier != RiskTier.R0:
            message = f"{label} is {decision.effective_tier.value}; quick runs only make read calls"
            entry.update(outcome="skipped", message=message)
            self._record(check, entry)
            if check:
                self.depth_skipped_in.add(check)
            return {"ok": False, "error": {"kind": "depth", "message": message}}

        if self.ctx.demo_mode:
            if self._demo is None:
                from app.script_runtime.demo_workspace import DemoWorkspace

                tags = dict(getattr(self.ctx.target, "tags", None) or {})
                self._demo = DemoWorkspace(tags, self.ctx.params)
            try:
                value, err = self._demo.respond(kind, msg)
            except Exception as exc:  # noqa: BLE001 - a bad argument is the script's error, not the run's
                value, err = None, classify_exception(exc)
            entry.update(demo=True, ms=0)
            if err:
                entry.update(outcome="error", message=err["message"][:500], error_kind=err["kind"])
                self._record(check, entry)
                return {"ok": False, "error": err}
            entry["outcome"] = "ok"
            self._record(check, entry)
            return {"ok": True, "value": value}

        if kind == "http":
            problem = _internal_destination(str(msg.get("url", "")))
            if problem:
                entry.update(outcome="blocked", message=problem)
                self._record(check, entry)
                if check:
                    self.blocked_in.add(check)
                return {"ok": False, "error": {"kind": "blocked", "message": problem}}
            value, err = _http_request(msg)
            entry["ms"] = int((time.monotonic() - t0) * 1000)
            if err:
                entry.update(outcome="error", message=err["message"][:500], error_kind=err["kind"])
                self._record(check, entry)
                return {"ok": False, "error": err}
            entry.update(outcome="ok", status_code=value["status_code"])
            self._record(check, entry)
            return {"ok": True, "value": value}

        try:
            client = self._client(role)
            if kind == "call":
                fn = getattr(getattr(client, msg["service"]), msg["method"])
                raw = fn(**sdk_catalog.coerce_kwargs(fn, kwargs))
            else:
                raw = client.api_client.do(op.verb, msg["path"], query=msg.get("query"),
                                           body=msg.get("body"))
            value = sdk_catalog.to_jsonable(raw)
        except Exception as exc:  # noqa: BLE001 - every failure is data for the script
            err = classify_exception(exc)
            entry.update(outcome="error", message=err["message"][:500], error_kind=err["kind"],
                         ms=int((time.monotonic() - t0) * 1000))
            self._record(check, entry)
            return {"ok": False, "error": err}
        entry.update(outcome="ok", ms=int((time.monotonic() - t0) * 1000))
        excerpt = json.dumps(value, default=str)
        entry["response_excerpt"] = excerpt[:600]
        self._record(check, entry)
        return {"ok": True, "value": value}


def _internal_destination(url: str) -> Optional[str]:
    """Why ``url`` must not be probed from the app's network, or None if it may.

    ``http()`` runs in the app's process: loopback reaches the app's own API and
    anything else on the host, and link-local reaches the cloud metadata service.
    Private ranges stay reachable because egress tests probe them on purpose.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None  # _http_request reports the DNS failure as the probe's result
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
            return (f"http() can't reach {host} ({ip}): loopback, link-local and cloud-metadata "
                    "addresses are off-limits to test scripts")
    return None


def _http_request(msg: dict) -> tuple[Optional[dict], Optional[dict]]:
    """Perform an egress probe from the app's network; map failures to their stage."""
    import socket
    from urllib.parse import urlparse

    import httpx

    url = str(msg.get("url"))
    host = urlparse(url).hostname or ""
    try:
        socket.getaddrinfo(host, None)
    except OSError as exc:
        return None, {"kind": "connect", "stage": "RESOLVE", "bucket": "DNS_RESOLUTION_FAILURE",
                      "message": f"DNS resolution failed for {host}: {exc}"}
    body = msg.get("body")
    kwargs: dict = {"headers": msg.get("headers") or {}}
    if isinstance(body, (dict, list)):
        kwargs["json"] = body
    elif body is not None:
        kwargs["content"] = str(body)
    timeout = min(float(msg.get("timeout") or 15.0), 60.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            resp = client.request(str(msg.get("method", "GET")), url, **kwargs)
    except httpx.TimeoutException as exc:
        return None, {"kind": "connect", "stage": "CONNECT", "bucket": "UPSTREAM_TIMEOUT",
                      "message": f"timed out reaching {host}: {exc}"}
    except httpx.ConnectError as exc:
        text = str(exc)
        if "ssl" in text.lower() or "certificate" in text.lower() or "tls" in text.lower():
            return None, {"kind": "connect", "stage": "TLS", "bucket": "TLS_HANDSHAKE_FAILURE",
                          "message": f"TLS handshake with {host} failed: {text}"}
        return None, {"kind": "connect", "stage": "CONNECT", "bucket": "NETWORK_POLICY_DENY",
                      "message": f"could not connect to {host}: {text}"}
    except httpx.HTTPError as exc:
        return None, {"kind": "connect", "stage": "CONNECT", "bucket": "UPSTREAM_TIMEOUT",
                      "message": f"request to {host} failed: {exc}"}
    return {"status_code": resp.status_code, "headers": dict(resp.headers),
            "text": resp.text[:4096]}, None


def classify_exception(exc: Exception) -> dict:
    """Map an SDK/transport exception onto the sandbox's error kinds."""
    message = str(exc) or type(exc).__name__
    if isinstance(exc, TypeError):
        return {"kind": "type", "message": message}
    try:
        from databricks.sdk import errors as dbx

        if isinstance(exc, dbx.PermissionDenied):
            return {"kind": "permission_denied", "message": message, "status": 403,
                    "error_code": getattr(exc, "error_code", None)}
        if isinstance(exc, dbx.Unauthenticated):
            return {"kind": "unauthenticated", "message": message, "status": 401}
        if isinstance(exc, (dbx.NotFound, getattr(dbx, "ResourceDoesNotExist", dbx.NotFound))):
            return {"kind": "not_found", "message": message, "status": 404,
                    "error_code": getattr(exc, "error_code", None)}
        if isinstance(exc, getattr(dbx, "NotImplemented", ())):
            return {"kind": "api", "message": message, "status": 501,
                    "error_code": getattr(exc, "error_code", None)}
        if isinstance(exc, dbx.DatabricksError):
            return {"kind": "api", "message": message, "error_code": getattr(exc, "error_code", None)}
    except ImportError:  # pragma: no cover - sdk is a hard dependency
        pass
    name = type(exc).__name__.lower()
    if "connection" in name or "timeout" in name:
        return {"kind": "connect", "message": message}
    try:
        from app.clients.databricks import IdentityError

        if isinstance(exc, IdentityError):
            return {"kind": "unauthenticated", "message": message}
    except ImportError:  # pragma: no cover
        pass
    return {"kind": "api", "message": message}


# ---------------------------------------------------------------------------
# Process lifecycle
# ---------------------------------------------------------------------------
_HARDENED = False


def _harden_parent() -> None:
    """On Linux, stop same-uid children reading this process's environment.

    Without this a script could read ``/proc/<app pid>/environ`` (where the app's
    OAuth client secret and Lakebase credentials live). ``PR_SET_DUMPABLE=0``
    makes ``/proc/<pid>`` root-owned and blocks ptrace.
    """
    global _HARDENED
    if _HARDENED or not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
    except Exception:  # noqa: BLE001 - best effort
        logger.warning("could not set PR_SET_DUMPABLE; script sandbox is weaker", exc_info=True)
    _HARDENED = True


def _sandbox_env(home: str) -> dict:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": home,
        "TMPDIR": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _target_public(target: Any) -> dict:
    if target is None:
        return {}
    return {"id": target.id, "name": target.name, "workspace_name": target.workspace_name,
            "host": target.host, "safety_class": target.safety_class.value,
            "tags": dict(target.tags or {}),
            "roles": [r.get("label") or r.get("group_id") for r in (target.assumable_roles or [])]}


def run_script(script: str, ctx: RunContext) -> ScriptResult:
    """Execute ``script`` against ``ctx.target`` and return every check's result."""
    _harden_parent()
    result = ScriptResult()
    broker = _Broker(ctx, result)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vp-test-") as home:
        stderr_path = os.path.join(home, ".stderr")
        with open(stderr_path, "w+") as stderr_file:
            proc = subprocess.Popen(
                [sys.executable, "-I", str(BOOTSTRAP), str(ctx.memory_mb), str(ctx.timeout_seconds)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_file,
                env=_sandbox_env(home), cwd=home, text=True, bufsize=1,
                start_new_session=True,
            )
            timer = threading.Timer(ctx.timeout_seconds, proc.kill)
            timer.start()
            try:
                _converse(proc, script, ctx, broker, result)
            finally:
                timer.cancel()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if proc.returncode not in (0, None) and result.error is None:
                stderr_file.seek(0)
                tail = stderr_file.read()[-2000:]
                timed_out = (time.monotonic() - started) >= ctx.timeout_seconds - 0.5
                result.timed_out = timed_out
                result.error = {
                    "kind": "timeout" if timed_out else "crash",
                    "message": (f"script exceeded the {ctx.timeout_seconds}s limit and was stopped"
                                if timed_out else f"sandbox exited with code {proc.returncode}"),
                    "traceback": tail or None,
                }
    result.duration_ms = int((time.monotonic() - started) * 1000)
    _finalize(result, broker)
    return result


def _converse(proc, script: str, ctx: RunContext, broker: _Broker, result: ScriptResult) -> None:
    init = {"type": "init", "script": script, "target": _target_public(ctx.target),
            "depth": ctx.depth.value if isinstance(ctx.depth, Depth) else str(ctx.depth),
            "params": ctx.params}
    try:
        proc.stdin.write(json.dumps(init, default=str) + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        return
    while True:
        line = proc.stdout.readline()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = msg.get("type")
        if kind in ("call", "api", "http"):
            reply = broker.handle(msg)
            reply["id"] = msg.get("id")
            try:
                proc.stdin.write(json.dumps(reply, default=str) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                return
        elif kind == "check_start":
            name = str(msg.get("name", ""))
            existing = result.check(name)
            if existing is not None and existing.outcome != "not_reached":
                if name not in broker.duplicates:
                    broker.duplicates.add(name)
                    existing.outcome, existing.stage, existing.bucket = "error", "OPERATE", "SCRIPT_ERROR"
                    existing.message = (f"check '{name}' ran more than once (in a loop?), so its runs can't "
                                        "be told apart. Give each check its own name, or loop inside one check.")
                continue
            result.checks.append(CheckResult(
                name=name, kind=msg.get("kind") or "assertion", role=msg.get("role"),
                advisory=bool(msg.get("advisory")), description=msg.get("description") or "",
                expected_stage=msg.get("expected_stage"), outcome="running"))
        elif kind == "check_end":
            res = result.check(str(msg.get("name", "")))
            if res is None or res.outcome != "running":
                continue
            res.outcome = msg.get("outcome") or "error"
            res.stage = msg.get("stage")
            res.bucket = msg.get("bucket")
            res.message = str(msg.get("message") or "")[:2000]
            res.traceback = msg.get("traceback")
            res.line = msg.get("line")
            res.evidence = msg.get("evidence") or {}
            res.ms = msg.get("ms")
        elif kind == "log":
            if len(result.logs) < 500:
                result.logs.append(str(msg.get("message", ""))[:2000])
        elif kind == "done":
            result.error = msg.get("error")
            result.output = str(msg.get("output") or "")
            return


def _finalize(result: ScriptResult, broker: _Broker) -> None:
    """Apply the broker's ground truth over what the script reported."""
    for res in result.checks:
        if res.name in broker.duplicates:
            continue
        if res.outcome == "running":
            # The script died inside this check.
            res.outcome = "error"
            res.stage = res.stage or "OPERATE"
            res.bucket = "SCRIPT_ERROR"
            res.message = (result.error or {}).get("message") or "the script stopped inside this check"
        if res.name in broker.blocked_in and res.outcome != "blocked":
            blocked = next((c for c in res.calls if c.get("outcome") == "blocked"), {})
            res.outcome, res.stage, res.bucket = "blocked", "AUTHORIZE", "BLOCKED_BY_SAFETY"
            res.message = blocked.get("message") or "a call in this check was blocked by the safety gate"
        elif res.name in broker.depth_skipped_in and res.outcome in ("pass", "fail", "error"):
            res.outcome, res.stage, res.bucket = "skipped", None, None
            res.message = "skipped at quick depth: this check makes a mutating call (run at full depth)"
