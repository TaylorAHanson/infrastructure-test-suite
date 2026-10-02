"""Static analysis of a Test script — run on save, in the editor, and by the agent.

Nothing is executed. It reports syntax errors, the checks the script declares
(``with check("…")`` / ``with expect_deny("…")``), every ``w.<service>.<method>``
call with its verb and risk tier, calls that don't exist in the SDK, and calls on
the R3 blocklist (which make the script unsaveable). The runtime gate still
re-checks every call as it happens; this is what lets a human see the blast
radius before saving.
"""
from __future__ import annotations

import ast
import difflib
from typing import Any, Optional

from app.core.enums import RiskTier
from app.core.operations import Operation, is_blocklisted
from app.script_runtime import sdk_catalog
from app.script_runtime.runner import _sql_operation, effective_verb, raw_api_operation
from app.services.risk import derive_risk_tier

_CHECK_FUNCS = {"check": "assertion", "expect_deny": "counterfactual"}
_RAW_VERBS = {"get", "post", "put", "patch", "delete"}


def _const(node: Optional[ast.AST]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _sql_text(node: Optional[ast.AST]) -> Optional[str]:
    """A statement's text for classification; an f-string's holes read as identifiers."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values)
    return None


def _proves_something(with_node: ast.With) -> bool:
    """A check body that can fail on its own terms: an assert, a raise, or skip()."""
    for inner in ast.walk(with_node):
        if isinstance(inner, (ast.Assert, ast.Raise)):
            return True
        if isinstance(inner, ast.Call) and _func_name(inner) == "skip":
            return True
    return False


def _kw(call: ast.Call, name: str) -> Optional[ast.AST]:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _func_name(call: ast.Call) -> Optional[str]:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "validation":
        return f.attr
    return None


def _w_chain(call: ast.Call) -> Optional[list[str]]:
    """``w.a.b(...)`` → ``["a", "b"]``; anything else → None."""
    parts: list[str] = []
    node: ast.AST = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and node.id == "w" and parts:
        return list(reversed(parts))
    return None


def analyze(script: str) -> dict:
    out: dict = {"ok": True, "errors": [], "warnings": [], "checks": [], "calls": [],
                 "max_risk_tier": RiskTier.R0.value}
    try:
        tree = ast.parse(script or "", filename="<test>")
    except SyntaxError as exc:
        out["ok"] = False
        out["errors"].append({"line": exc.lineno, "message": f"Syntax error: {exc.msg}"})
        return out

    seen: dict[str, int] = {}
    check_nodes: list[tuple[ast.With, ast.Call]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call) and _func_name(call) in _CHECK_FUNCS:
                    check_nodes.append((node, call))

    for with_node, call in sorted(check_nodes, key=lambda x: x[1].lineno):
        fname = _func_name(call)
        name = _const(call.args[0]) if call.args else _const(_kw(call, "name"))
        if not isinstance(name, str) or not name.strip():
            out["warnings"].append({
                "line": call.lineno,
                "message": "Check names should be plain string literals so each check keeps its run history."})
            continue
        name = name.strip()
        if name in seen:
            out["ok"] = False
            out["errors"].append({"line": call.lineno,
                                  "message": f"Duplicate check name '{name}' (first used on line {seen[name]})."})
            continue
        seen[name] = call.lineno
        role = _const(_kw(call, "role"))
        at = _const(_kw(call, "at"))
        out["checks"].append({
            "name": name, "kind": _CHECK_FUNCS[fname], "line": call.lineno,
            "role": role if isinstance(role, str) else None,
            "expected_stage": (str(at).upper() if at else "AUTHORIZE") if fname == "expect_deny" else None,
            "advisory": bool(_const(_kw(call, "advisory"))),
            "description": _const(_kw(call, "description")) or "",
        })
        if fname == "check" and not _proves_something(with_node):
            out["warnings"].append({
                "line": call.lineno,
                "message": f"Check '{name}' has no assert, so it passes whenever its calls don't raise. "
                           "Assert on what the result should contain."})
        for inner in ast.walk(with_node):
            if inner is with_node or not isinstance(inner, (ast.With, ast.AsyncWith)):
                continue
            for item in inner.items:
                c = item.context_expr
                if isinstance(c, ast.Call) and _func_name(c) in _CHECK_FUNCS:
                    out["ok"] = False
                    out["errors"].append({"line": c.lineno, "message": "Checks can't be nested inside another check."})

    if not out["checks"] and not out["errors"]:
        out["warnings"].append({"line": None, "message": "This script declares no checks, so a run records nothing. "
                                "Wrap what you verify in `with check(\"…\"):`."})

    max_tier = RiskTier.R0
    services = sdk_catalog.catalog()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        op: Optional[Operation] = None
        label = ""
        chain = _w_chain(node)
        if chain and len(chain) == 2 and chain[0] == "api" and chain[1] in _RAW_VERBS:
            path = _const(node.args[0]) if node.args else None
            if isinstance(path, str):
                op = raw_api_operation(chain[1].upper(), path)
                label = f"{chain[1].upper()} {path}"
            else:
                out["warnings"].append({"line": node.lineno, "message": "w.api path isn't a literal; its tier is checked at run time."})
                continue
        elif chain and len(chain) == 2:
            service, method = chain
            label = f"w.{service}.{method}"
            if service not in services:
                close = difflib.get_close_matches(service, list(services), n=3)
                out["ok"] = False
                out["errors"].append({"line": node.lineno, "message": f"`w.{service}` is not a Databricks SDK service"
                                      + (f" — did you mean {', '.join('w.' + c for c in close)}?" if close else ".")})
                continue
            if method not in services[service].methods:
                close = difflib.get_close_matches(method, list(services[service].methods), n=3)
                out["ok"] = False
                out["errors"].append({"line": node.lineno, "message": f"`w.{service}` has no method `{method}`"
                                      + (f" — did you mean {', '.join(close)}?" if close else ".")})
                continue
            kwargs = {k.arg: _const(k.value) for k in node.keywords if k.arg}
            if node.args:
                out["warnings"].append({"line": node.lineno, "message": f"{label}() takes keyword arguments only."})
            if service == "statement_execution" and method == "execute_statement":
                op = _sql_operation(str(kwargs.get("statement") or ""))
            else:
                op = Operation(service=service, method=method, verb=effective_verb(service, method, kwargs))
        elif _func_name(node) == "sql":
            stmt = _sql_text(node.args[0] if node.args else _kw(node, "statement"))
            label = "sql(...)"
            op = _sql_operation(stmt or "")
            if stmt is None:
                out["warnings"].append({"line": node.lineno, "message": "sql() statement isn't a literal; it's classified at run time."})
        if op is None:
            continue
        blocked, reason = is_blocklisted(op)
        tier = RiskTier.R3 if blocked else derive_risk_tier(op)
        out["calls"].append({"op": label, "verb": op.verb, "tier": tier.value, "line": node.lineno,
                             "blocklisted": blocked})
        if blocked:
            out["ok"] = False
            out["errors"].append({"line": node.lineno,
                                  "message": f"{label} is on the R3 blocklist and can never run: {reason}"})
        if tier.order > max_tier.order:
            max_tier = tier
    out["max_risk_tier"] = max_tier.value
    return out
