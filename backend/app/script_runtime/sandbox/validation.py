"""The library a Test script runs against. Pure stdlib — it never imports the app.

A script gets these names pre-bound (``from validation import *`` also works)::

    w            the Databricks workspace client, bound to the target's identity
    check        ``with check("name"):`` — one result; passes if the block completes
    expect_deny  ``with expect_deny("name", at="AUTHORIZE"):`` — passes if the block is denied
    sql          ``sql("SELECT 1")`` — run a statement on a SQL warehouse, rows as dicts
    http         ``http("https://pypi.org/simple/")`` — an egress probe from the app's network
    record       ``record("key", value)`` — attach evidence to the current check
    skip         ``skip("why")`` — end the current check as skipped (not configured, not applicable)
    setting      ``setting("canary_catalog", "validation_canary")`` — a run param, else a target tag
    log          ``log("...")`` — a line in the run log
    target       the target being tested (name, workspace_name, host, safety_class, tags)
    depth        "quick" or "full"
    params       per-run parameters (dict)

``w`` holds no credentials. Every call is a request to the platform, which applies
the safety gate (blocklist, the target's safety-class cap, the run depth) and then
makes the call as the target's service principal (or the check's ``role``).
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Any, Optional

__all__ = [
    "w", "check", "expect_deny", "sql", "http", "record", "skip", "setting", "log", "target",
    "depth", "params", "ApiError", "PermissionDenied", "Unauthenticated", "NotFound",
    "ConnectionFailed", "SafetyBlocked", "DepthSkipped", "Skipped",
]

_RPC_IN = None
_RPC_OUT = None
_SEQ = 0
_CURRENT: Optional["_Check"] = None
_DECLARED: list[str] = []


def _init(rpc_in, rpc_out) -> None:
    global _RPC_IN, _RPC_OUT
    _RPC_IN, _RPC_OUT = rpc_in, rpc_out


def _send(msg: dict) -> None:
    _RPC_OUT.write(json.dumps(msg, default=str) + "\n")
    _RPC_OUT.flush()


def _request(msg: dict) -> Any:
    global _SEQ
    _SEQ += 1
    msg["id"] = _SEQ
    if _CURRENT is not None:
        msg["check"] = _CURRENT.name
        msg["role"] = _CURRENT.role
    frame = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename != "<test>":
        frame = frame.f_back
    if frame is not None:
        msg["line"] = frame.f_lineno
    _send(msg)
    line = _RPC_IN.readline()
    if not line:
        raise SystemExit("platform closed the channel")
    reply = json.loads(line)
    if reply.get("ok"):
        return _wrap(reply.get("value"))
    _raise(reply.get("error") or {})


# ---------------------------------------------------------------------------
# Errors a script can catch
# ---------------------------------------------------------------------------
class ApiError(Exception):
    """A Databricks API call failed."""

    def __init__(self, message: str, error_code: Optional[str] = None, status: Optional[int] = None):
        super().__init__(message)
        self.error_code = error_code
        self.status = status


class PermissionDenied(ApiError):
    """The identity is not allowed to do this (HTTP 403)."""


class Unauthenticated(ApiError):
    """The identity could not authenticate (HTTP 401)."""


class NotFound(ApiError):
    """The resource does not exist (HTTP 404)."""


class ConnectionFailed(ApiError):
    """The endpoint could not be reached (DNS, connect, TLS, or timeout)."""

    def __init__(self, message: str, error_code: Optional[str] = None, status: Optional[int] = None,
                 stage: str = "CONNECT", bucket: str = "UPSTREAM_TIMEOUT"):
        super().__init__(message, error_code, status)
        self.stage = stage
        self.bucket = bucket


class SafetyBlocked(Exception):
    """The platform's safety gate refused this call; it was never sent."""


class DepthSkipped(Exception):
    """A mutating call in a ``quick`` run; it was skipped, not sent."""


class Skipped(Exception):
    """Raised by ``skip()``: the check doesn't apply to this target."""


_ERRORS = {
    "permission_denied": PermissionDenied,
    "unauthenticated": Unauthenticated,
    "not_found": NotFound,
    "connect": ConnectionFailed,
    "api": ApiError,
}


def _raise(err: dict):
    kind = err.get("kind", "api")
    message = err.get("message", "call failed")
    if kind == "blocked":
        raise SafetyBlocked(message)
    if kind == "depth":
        raise DepthSkipped(message)
    if kind == "invalid":
        raise AttributeError(message)
    if kind == "type":
        raise TypeError(message)
    if kind == "connect":
        raise ConnectionFailed(message, err.get("error_code"), err.get("status"),
                               stage=err.get("stage") or "CONNECT", bucket=err.get("bucket") or "UPSTREAM_TIMEOUT")
    cls = _ERRORS.get(kind, ApiError)
    raise cls(message, error_code=err.get("error_code"), status=err.get("status"))


# ---------------------------------------------------------------------------
# Results: dicts that also allow attribute access (cluster.cluster_id)
# ---------------------------------------------------------------------------
def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


class Obj(dict):
    """A response object. ``me.user_name`` also finds SCIM's camelCase ``userName``."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        if name in self:
            return self[name]
        return self.get(_camel(name))

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def _wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return Obj({k: _wrap(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# The workspace client proxy
# ---------------------------------------------------------------------------
class _Service:
    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)
        service = self._name

        def call(*args, **kwargs):
            if args:
                raise TypeError(f"w.{service}.{method}() takes keyword arguments only")
            return _request({"type": "call", "service": service, "method": method, "kwargs": kwargs})

        call.__name__ = method
        return call

    def __repr__(self) -> str:
        return f"<w.{self._name}>"


class _Api:
    """Raw REST access: ``w.api.get("/api/2.0/clusters/list")``."""

    def _do(self, verb: str, path: str, body: Any = None, query: Optional[dict] = None):
        return _request({"type": "api", "verb": verb, "path": path, "body": body, "query": query})

    def get(self, path: str, query: Optional[dict] = None):
        return self._do("GET", path, query=query)

    def post(self, path: str, body: Any = None, query: Optional[dict] = None):
        return self._do("POST", path, body, query)

    def put(self, path: str, body: Any = None, query: Optional[dict] = None):
        return self._do("PUT", path, body, query)

    def patch(self, path: str, body: Any = None, query: Optional[dict] = None):
        return self._do("PATCH", path, body, query)

    def delete(self, path: str, body: Any = None, query: Optional[dict] = None):
        return self._do("DELETE", path, body, query)


class _Workspace:
    api = _Api()

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return _Service(name)

    def __repr__(self) -> str:
        return "<WorkspaceClient (proxied through the platform safety gate)>"


w = _Workspace()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _assert_message(exc: AssertionError, tb) -> str:
    if str(exc):
        return str(exc)
    frames = [f for f in traceback.extract_tb(tb) if f.filename == "<test>"]
    if frames and frames[-1].line:
        return f"assertion failed: {frames[-1].line.strip()}"
    return "assertion failed"


def _script_line(tb) -> Optional[int]:
    frames = [f for f in traceback.extract_tb(tb) if f.filename == "<test>"]
    return frames[-1].lineno if frames else None


class _Check:
    kind = "assertion"

    def __init__(self, name: str, *, role: Optional[str] = None, advisory: bool = False,
                 description: str = ""):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a check needs a non-empty name")
        self.name = name.strip()
        self.role = role
        self.advisory = bool(advisory)
        self.description = description
        self.evidence: dict = {}
        self._t0 = 0.0

    def _start_msg(self) -> dict:
        return {"type": "check_start", "name": self.name, "kind": self.kind, "role": self.role,
                "advisory": self.advisory, "description": self.description}

    def __enter__(self):
        global _CURRENT
        if _CURRENT is not None:
            raise RuntimeError(f"check '{self.name}' is nested inside '{_CURRENT.name}'; checks can't nest")
        _CURRENT = self
        self._t0 = time.monotonic()
        _send(self._start_msg())
        return self

    def _end(self, outcome: str, stage: Optional[str] = None, bucket: Optional[str] = None,
             message: str = "", tb_text: Optional[str] = None, line: Optional[int] = None):
        global _CURRENT
        _send({"type": "check_end", "name": self.name, "outcome": outcome, "stage": stage,
               "bucket": bucket, "message": message, "traceback": tb_text, "line": line,
               "evidence": self.evidence, "ms": int((time.monotonic() - self._t0) * 1000)})
        _CURRENT = None

    def _classify_exception(self, exc_type, exc, tb):
        line = _script_line(tb)
        if issubclass(exc_type, SafetyBlocked):
            return ("blocked", "AUTHORIZE", "BLOCKED_BY_SAFETY", str(exc), line)
        if issubclass(exc_type, (DepthSkipped, Skipped)):
            return ("skipped", None, None, str(exc), line)
        if issubclass(exc_type, AssertionError):
            return ("fail", "VERIFY", "UNEXPECTED_RESULT", _assert_message(exc, tb), line)
        if issubclass(exc_type, PermissionDenied):
            return ("fail", "AUTHORIZE", "PERMISSION_DENIED", str(exc), line)
        if issubclass(exc_type, Unauthenticated):
            return ("fail", "AUTHENTICATE", "AUTHENTICATION_FAILURE", str(exc), line)
        if issubclass(exc_type, ConnectionFailed):
            return ("fail", exc.stage, exc.bucket, str(exc), line)
        if issubclass(exc_type, ApiError):
            bucket = "FEATURE_NOT_ENABLED" if exc.status == 501 else "OPERATION_FAILED"
            return ("fail", "OPERATE", bucket, str(exc), line)
        return ("error", "OPERATE", "SCRIPT_ERROR", f"{exc_type.__name__}: {exc}", line)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._end("pass")
            return False
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            self._end("error", "OPERATE", "SCRIPT_ERROR", "interrupted")
            return False
        outcome, stage, bucket, message, line = self._classify_exception(exc_type, exc, tb)
        self._end(outcome, stage, bucket, message,
                  "".join(traceback.format_exception(exc_type, exc, tb))[-4000:], line)
        return True  # a failed check never stops the script


class _ExpectDeny(_Check):
    kind = "counterfactual"

    def __init__(self, name: str, *, at: str = "AUTHORIZE", role: Optional[str] = None,
                 advisory: bool = False, description: str = ""):
        super().__init__(name, role=role, advisory=advisory, description=description)
        self.at = (at or "AUTHORIZE").upper()

    def _start_msg(self) -> dict:
        return {**super()._start_msg(), "expected_stage": self.at}

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._end("fail", "AUTHORIZE", "COUNTERFACTUAL_NOT_DENIED",
                      f"expected a denial at {self.at}, but the block succeeded")
            return False
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            self._end("error", "OPERATE", "SCRIPT_ERROR", "interrupted")
            return False
        denied_at = None
        if issubclass(exc_type, PermissionDenied):
            denied_at = "AUTHORIZE"
        elif issubclass(exc_type, Unauthenticated):
            denied_at = "AUTHENTICATE"
        elif issubclass(exc_type, ConnectionFailed):
            denied_at = exc.stage
        elif issubclass(exc_type, ApiError):
            denied_at = "OPERATE"
        network = ("RESOLVE", "CONNECT", "TLS")
        # A broken identity or an outage is never the denial a counterfactual asked for.
        if denied_at and (denied_at == self.at
                          or (self.at == "OPERATE" and denied_at == "AUTHORIZE")
                          or (self.at == "CONNECT" and denied_at in network)):
            self.evidence.setdefault("denied", {"stage": denied_at, "message": str(exc)})
            self._end("pass", denied_at, None, f"denied as expected: {exc}")
            return True
        if denied_at:
            self._end("fail", denied_at, "UNEXPECTED_RESULT",
                      f"denied at {denied_at}, expected {self.at}: {exc}")
            return True
        return super().__exit__(exc_type, exc, tb)


def check(name: str, *, role: Optional[str] = None, advisory: bool = False,
          description: str = "") -> _Check:
    """One result. Passes when the block finishes; fails on an assert or API error.

    ``role`` runs the block as a registered role-profile instead of the target's
    service principal. ``advisory=True`` records the result without gating.
    """
    return _Check(name, role=role, advisory=advisory, description=description)


def expect_deny(name: str, *, at: str = "AUTHORIZE", role: Optional[str] = None,
                advisory: bool = False, description: str = "") -> _ExpectDeny:
    """A counterfactual: passes when something in the block is denied at ``at``.

    ``at="AUTHORIZE"`` (default) wants a permission denial (HTTP 403, or a SQL
    ``INSUFFICIENT_PERMISSIONS`` error); ``at="CONNECT"`` wants a network denial
    (DNS, connect or TLS); ``at="OPERATE"`` accepts a permission denial or any
    other API error. An authentication failure or an outage never counts.
    Succeeding is a failure: it should have been blocked.
    """
    return _ExpectDeny(name, at=at, role=role, advisory=advisory, description=description)


def record(key: str, value: Any) -> None:
    """Attach a piece of evidence to the current check (shown in run details)."""
    if _CURRENT is None:
        raise RuntimeError("record() must be called inside a check")
    _CURRENT.evidence[str(key)] = value


def skip(reason: str) -> None:
    """End the current check as skipped — e.g. the target isn't configured for it."""
    if _CURRENT is None:
        raise RuntimeError("skip() must be called inside a check")
    raise Skipped(reason)


def setting(key: str, default: Any = None) -> Any:
    """A per-run param, else the target's tag of the same name, else ``default``.

    Tags are where a workspace's specifics live (its expected metastore id, its
    canary catalog), so one script serves every target.
    """
    value = params.get(key)
    if value in (None, ""):
        value = (target.get("tags") or {}).get(key)
    return default if value in (None, "") else value


def log(*parts: Any) -> None:
    _send({"type": "log", "message": " ".join(str(p) for p in parts)})


_SQL_DENIED = ("INSUFFICIENT_PERMISSIONS", "PERMISSION_DENIED", "UNAUTHORIZED_ACCESS")
_SQL_MISSING = ("TABLE_OR_VIEW_NOT_FOUND", "SCHEMA_NOT_FOUND", "CATALOG_NOT_FOUND",
                "NO_SUCH_CATALOG_EXCEPTION", "PATH_NOT_FOUND", "VOLUME_NOT_FOUND")


def _sql_error(status: dict, state: Optional[str]) -> ApiError:
    err = status.get("error") or {}
    message = err.get("message") or f"statement {state}"
    code = err.get("error_code")
    text = f"{code or ''} {message}".upper()
    if any(k in text for k in _SQL_DENIED):
        return PermissionDenied(message, error_code=code, status=403)
    if any(k in text for k in _SQL_MISSING):
        return NotFound(message, error_code=code, status=404)
    return ApiError(message, error_code=code)


def _default_warehouse() -> str:
    warehouses = w.warehouses.list()
    if not warehouses:
        raise ApiError("no SQL warehouse is available on this target")
    ranked = sorted(warehouses, key=lambda x: ((x.get("state") or "") != "RUNNING",
                                                not x.get("enable_serverless_compute")))
    return ranked[0]["id"]


def sql(statement: str, *, warehouse_id: Optional[str] = None, catalog: Optional[str] = None,
        schema: Optional[str] = None, timeout: str = "30s", max_wait: float = 300) -> list:
    """Run one SQL statement on a warehouse; returns rows as dicts (values are strings).

    Uses ``warehouse_id``, else ``setting("warehouse_id")``, else a running (or
    serverless) warehouse. A statement still running after ``timeout`` is polled
    until it finishes or ``max_wait`` seconds pass (a cold warehouse takes a while).
    A permission error raises ``PermissionDenied``; a missing object ``NotFound``.
    """
    wid = warehouse_id or setting("warehouse_id") or _default_warehouse()
    resp = w.statement_execution.execute_statement(
        statement=statement, warehouse_id=wid, catalog=catalog, schema=schema,
        wait_timeout=timeout)
    deadline = time.monotonic() + max_wait
    status = resp.get("status") or {}
    while status.get("state") in ("PENDING", "RUNNING") and resp.get("statement_id"):
        if time.monotonic() > deadline:
            raise ApiError(f"statement still {status.get('state')} after {int(max_wait)}s")
        time.sleep(3)
        resp = w.statement_execution.get_statement(statement_id=resp["statement_id"])
        status = resp.get("status") or {}
    state = status.get("state")
    if state not in (None, "SUCCEEDED"):
        raise _sql_error(status, state)
    cols = [c.get("name") for c in (((resp.get("manifest") or {}).get("schema") or {}).get("columns") or [])]
    rows = ((resp.get("result") or {}).get("data_array")) or []
    return [Obj(zip(cols, r)) for r in rows]


def http(url: str, *, method: str = "GET", headers: Optional[dict] = None, body: Any = None,
         timeout: float = 15.0) -> Obj:
    """Make an HTTP request from the app's network — proves an egress path is open.

    Returns ``status_code``, ``headers`` and ``text`` (first 4 KB). A DNS, connect
    or TLS failure raises ``ConnectionFailed`` with the stage it failed at.
    GET/HEAD/OPTIONS are reads; other methods are gated like any write.
    """
    return _request({"type": "http", "url": url, "method": str(method).upper(),
                     "headers": headers or {}, "body": body, "timeout": float(timeout)})


# Filled in by the bootstrap from the platform's init message.
target: Obj = Obj()
depth: str = "quick"
params: Obj = Obj()
