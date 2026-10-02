"""Script text the platform writes: the new-Test template and legacy-Test conversion."""
from __future__ import annotations

import builtins
import keyword
import re
from typing import Any, Iterable, Optional

#: Keys in a legacy Check's params that steer the platform, not the API call.
_CONTROL_KEYS = {"demo_outcome", "demo_evidence", "demo_row_count", "demo_status_code",
                 "expected_status", "cleanup", "_target_tags", "wait_timeout"}


def new_test_script(name: str = "", description: str = "") -> str:
    title = name.strip() or "New test"
    about = description.strip() or "What this test proves about a workspace, in a sentence or two."
    return f'''"""{title}

{about}
"""
# `w` is the Databricks SDK WorkspaceClient, running as the target's identity.
# Each `with check("...")` block is one result: it passes when the block
# finishes, and fails on an `assert` or an API error. Every call goes through
# the safety gate for the target's safety class; quick runs make only reads.

with check("the identity authenticates"):
    me = w.current_user.me()
    record("identity", me.user_name)
    assert me.active is not False, f"{me.user_name} is deactivated"

with check("at least one SQL warehouse exists"):
    warehouses = w.warehouses.list()
    record("warehouses", [wh.name for wh in warehouses])
    assert warehouses, "no SQL warehouses on this workspace"
'''


_RESERVED = set(dir(builtins)) | set(keyword.kwlist) | {
    "w", "check", "expect_deny", "sql", "http", "record", "skip", "setting", "log", "target",
    "depth", "params"}


def _ident(text: str, fallback: str = "result") -> str:
    out = re.sub(r"[^a-z0-9_]", "_", (text or "").lower()).strip("_")
    out = re.sub(r"_+", "_", out)
    if not out or out[0].isdigit():
        return fallback
    out = out[:30]
    return f"{out}_result" if out in _RESERVED else out


def _var_for(check: Any) -> str:
    """A name that says what the result is: ``warehouses``, ``catalog``, ``rows``."""
    if check.handler_key == "sql_warehouse":
        return "rows"
    if check.handler_key == "outbound_http":
        return "resp"
    op = dict(check.operation or {})
    service, method = str(op.get("service") or ""), str(op.get("method") or "")
    if not service:
        return _ident(check.display_name)
    if method == "me":
        return "me"
    if method.startswith("list"):
        rest = method[len("list"):].strip("_")
        return _ident(rest or service)
    if method in ("get", "current", "get_status"):
        base = service[:-1] if service.endswith("s") and not service.endswith("ss") else service
        return _ident(base)
    return _ident(f"{method}_{service}" if len(method) < 8 else method)


def _kwargs(params: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in params.items())


def _ref(value: Any, var: str) -> str:
    """``"$response.run_id"`` → ``var["run_id"]``; plain values stay literal."""
    if isinstance(value, str) and value.startswith("$"):
        parts = [p for p in value[1:].split(".") if p]
        if parts and parts[0] == "response":
            parts = parts[1:]
        return var + "".join(f"[{p!r}]" for p in parts)
    return repr(value)


_OPS = {"==": "==", ">=": ">=", "<=": "<=", ">": ">", "<": "<"}


def _assert_lines(check: Any, var: str) -> list[str]:
    predicate, assertion = check.compiled_predicate, check.assertion or ""
    if predicate and predicate.get("kind") == "compare":
        field, op, value = predicate.get("field"), _OPS.get(predicate.get("op"), "=="), predicate.get("value")
        if field == "row_count":
            return [f"assert len({var}) {op} {value!r}, {assertion!r}"]
        if field == "status_code":
            return [f"assert {var}.status_code {op} {value!r}, {assertion!r}"]
        demo = dict(dict((check.operation or {}).get("params") or {}).get("demo_evidence") or {})
        note = ([f"# TODO: '{field}' came from demo data, not the API's response. Check what",
                 f"# {var} really contains and assert on that instead."] if field in demo else [])
        return note + [f"assert {var}.get({field!r}) {op} {value!r}, {assertion!r}"]
    if assertion:
        return [f"# TODO: the original check only required this call to succeed, so nothing here",
                f"# proves \"{assertion}\". Assert on {var}, e.g. `assert {var}, \"...\"`."]
    return [f"# TODO: assert on {var} so this check proves more than reachability."]


def _body(check: Any, var: str) -> list[str]:
    op = dict(check.operation or {})
    params = {k: v for k, v in dict(op.get("params") or {}).items() if k not in _CONTROL_KEYS}
    handler = check.handler_key
    if handler == "sql_warehouse":
        statement = params.pop("statement", None) or params.pop("query", None) or "SELECT 1"
        extra = f", warehouse_id={params['warehouse_id']!r}" if params.get("warehouse_id") else ""
        return [f"{var} = sql({statement!r}{extra})", f"record('row_count', len({var}))"]
    if handler == "outbound_http":
        method = str(params.get("method", "GET")).upper()
        url = params.get("url") or "https://example.com"
        extra = f", method={method!r}" if method != "GET" else ""
        return [f"{var} = http({url!r}{extra})", f"record('status_code', {var}.status_code)"]
    if handler == "lakebase":
        return ["# The app's own Lakebase store isn't reachable from a test script.",
                "# Probe a workspace resource instead, e.g. w.database.list_database_instances().",
                f"{var} = w.current_user.me()"]
    service, method = op.get("service"), op.get("method")
    if not service or not method:
        return [f"{var} = w.current_user.me()  # TODO: the original check had no operation"]
    call = f"w.{service}.{method}({_kwargs(params)})"
    plan = dict(op.get("params") or {}).get("cleanup")
    if isinstance(plan, dict) and plan.get("service") and plan.get("method"):
        cleanup_args = ", ".join(f"{k}={_ref(v, var)}" for k, v in dict(plan.get("params") or {}).items())
        return [f"{var} = {call}",
                "try:",
                f"    record('response', {var})",
                "finally:",
                f"    w.{plan['service']}.{plan['method']}({cleanup_args})"]
    return [f"{var} = {call}"]


def structured_to_script(test: Any, members: Iterable[tuple[Any, Any]]) -> str:
    """Render a legacy structured Test (ordered ``(TestCheck, Check)``) as a script."""
    lines = [f'"""{test.name}', ""]
    if test.description:
        lines += [test.description, ""]
    lines += ["Converted from the structured (YAML) form.", '"""', ""]
    used: set[str] = set()
    for member, check in members:
        name = check.display_name or check.id
        base, n = name, 2
        while name in used:
            name, n = f"{base} ({n})", n + 1
        used.add(name)
        var = _var_for(check)
        role = member.run_as_role
        role_arg = f", role={role!r}" if role else ""
        counterfactual = member.kind == "counterfactual" or getattr(check.expectation, "value", check.expectation) == "DENY"
        advisory = getattr(check.evaluator, "value", check.evaluator) == "agentic"
        if check.assertion:
            lines.append(f"# {check.assertion}")
        if counterfactual:
            at = (check.expected_stage or "AUTHORIZE").upper()
            at_arg = "" if at == "AUTHORIZE" else f", at={at!r}"
            lines.append(f"with expect_deny({name!r}{at_arg}{role_arg}):")
            body = _body(check, var)
        else:
            adv = ", advisory=True" if advisory else ""
            lines.append(f"with check({name!r}{role_arg}{adv}):")
            body = _body(check, var) + _assert_lines(check, var)
            if advisory:
                body.append("# Judged by hand before: make this an assert to let it gate.")
        lines += ["    " + b for b in body] + [""]
    if not used:
        lines += ['with check("placeholder"):', "    w.current_user.me()", ""]
    return "\n".join(lines).rstrip() + "\n"
