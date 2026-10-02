"""Sandbox entrypoint: ``python -I bootstrap.py``. Pure stdlib.

The platform writes one JSON ``init`` line (script source, target, depth, params)
on stdin, then answers each call request on stdin. Everything the script prints
is captured; stdout carries only protocol messages. The process is started with
an environment holding no credentials, so the only way to reach Databricks is
through ``w``, i.e. through the platform's safety gate.
"""
from __future__ import annotations

import io
import json
import linecache
import os
import sys
import traceback

# -I (isolated mode) does not put the script's directory on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _limit_resources(memory_mb: int, cpu_seconds: int) -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return
    for limit, value in ((getattr(resource, "RLIMIT_AS", None), memory_mb * 1024 * 1024),
                         (getattr(resource, "RLIMIT_CPU", None), cpu_seconds)):
        if limit is None or value <= 0:
            continue
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass  # not enforceable on this platform (e.g. RLIMIT_AS on macOS)


def _deny_direct_network() -> None:
    """Steer scripts to ``w`` / ``sql()`` / ``http()``, which are gated and recorded."""
    import socket

    def refuse(*_args, **_kwargs):
        raise PermissionError("test scripts can't open network connections directly; "
                              "use w.<service>, sql() or http() so the call is gated and recorded")

    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    socket.create_connection = refuse


def main() -> None:
    args = sys.argv[1:]
    if len(args) >= 2:
        _limit_resources(int(args[0]), int(args[1]) + 5)
    _deny_direct_network()
    rpc_in = sys.stdin
    rpc_out = sys.stdout
    captured = io.StringIO()
    sys.stdin = io.StringIO("")
    sys.stdout = captured
    sys.stderr = captured

    import validation

    validation._init(rpc_in, rpc_out)
    init = json.loads(rpc_in.readline() or "{}")
    validation.target.update(init.get("target") or {})
    validation.depth = init.get("depth") or "quick"
    validation.params.update(init.get("params") or {})

    source = init.get("script") or ""
    scope = {"__name__": "__test__", "__file__": "<test>"}
    scope.update({name: getattr(validation, name) for name in validation.__all__})

    error = None
    linecache.cache["<test>"] = (len(source), None, source.splitlines(True), "<test>")
    try:
        code = compile(source, "<test>", "exec")
        exec(code, scope)  # noqa: S102 - this *is* the sandboxed test script
    except validation.DepthSkipped as exc:
        error = {"kind": "depth", "message": str(exc)}
    except validation.SafetyBlocked as exc:
        error = {"kind": "blocked", "message": str(exc),
                 "traceback": traceback.format_exc()[-4000:]}
    except SyntaxError as exc:
        error = {"kind": "syntax", "message": f"{exc.msg} (line {exc.lineno})", "line": exc.lineno}
    except BaseException as exc:  # noqa: BLE001 - report everything back
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<test>"]
        error = {"kind": "error", "message": f"{type(exc).__name__}: {exc}",
                 "line": frames[-1].lineno if frames else None,
                 "traceback": traceback.format_exc()[-4000:]}
    rpc_out.write(json.dumps({"type": "done", "error": error,
                              "output": captured.getvalue()[-20000:]}, default=str) + "\n")
    rpc_out.flush()


if __name__ == "__main__":
    main()
