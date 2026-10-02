"""Sandbox entrypoint: ``python -I bootstrap.py``. Pure stdlib.

The platform writes one JSON ``init`` line (script source, target, depth, params)
on stdin, then answers each call request on stdin. Everything the script prints
is captured; stdout carries only protocol messages. The process is started with
an environment holding no credentials, so the only way to reach Databricks is
through ``w``, i.e. through the platform's safety gate. Before the script runs,
an audit hook (``_install_guard``) refuses processes, sockets, native code and
files outside the standard library and the scratch home.
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


_DENIED_EVENTS = {
    "os.system": "start processes", "os.exec": "start processes", "os.posix_spawn": "start processes",
    "os.spawn": "start processes", "os.fork": "start processes", "os.forkpty": "start processes",
    "subprocess.Popen": "start processes", "pty.spawn": "start processes",
    "os.kill": "signal other processes", "os.killpg": "signal other processes",
    "signal.pthread_kill": "signal other processes", "sys.remote_exec": "attach to other processes",
    "gc.get_objects": "walk the interpreter's objects", "gc.get_referrers": "walk the interpreter's objects",
    "gc.get_referents": "walk the interpreter's objects",
    "sqlite3.enable_load_extension": "load native code", "sqlite3.load_extension": "load native code",
    "resource.setrlimit": "change their resource limits", "resource.prlimit": "change their resource limits",
}
_DENIED_MODULES = ("ctypes", "_ctypes", "_posixsubprocess", "_test", "_xx", "xx", "_interp", "_remote_debugging")
_WRITE_EVENTS = {"os.chmod", "os.chown", "os.chflags", "os.lchflags", "os.lchmod", "os.link", "os.mkdir",
                 "os.remove", "os.rename", "os.rmdir", "os.symlink", "os.truncate", "os.utime",
                 "os.setxattr", "os.removexattr", "shutil.rmtree", "shutil.move", "shutil.chown"}
_LIST_EVENTS = {"os.listdir", "os.scandir"}


def _install_guard(home: str) -> None:
    """Refuse what a test script never needs: processes, sockets, native code, the host's files.

    An audit hook is not a security boundary by itself (PEP 578); the boundary is
    that this process holds no credentials. The hook closes the easy paths around
    ``w``: scripts can't start or signal processes, open sockets, use ctypes or
    load a native extension from outside the standard library, read files outside
    the standard library, the sandbox and the scratch home, or write outside the
    scratch home. Third-party packages are dropped from the import path.
    """
    real = os.path.realpath
    third_party = ("site-packages", "dist-packages")
    sys.path[:] = [p for p in sys.path if p and not p.rstrip(os.sep).endswith(third_party)]
    sys.path_importer_cache.clear()
    code_roots = tuple(real(p) for p in sys.path)
    home = real(home)
    read_roots = code_roots + (home, real("/usr/share/zoneinfo"), "/dev/null", "/dev/urandom")
    write_roots = (home, "/dev/null")
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def inside(path, roots) -> bool:
        resolved = real(os.fsdecode(path))
        return any(resolved == r or resolved.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

    def require(path, roots, verb: str) -> None:
        if isinstance(path, int):
            return
        if not inside(path, roots):
            raise PermissionError(f"test scripts can't {verb} {os.fsdecode(path)}")

    def guard(event: str, args: tuple) -> None:
        if event in _DENIED_EVENTS:
            raise PermissionError(f"test scripts can't {_DENIED_EVENTS[event]}")
        if event.startswith("socket."):
            raise PermissionError("test scripts can't open network connections directly; "
                                  "use w.<service>, sql() or http() so the call is gated and recorded")
        if event.startswith("ctypes."):
            raise PermissionError("test scripts can't call native code")
        if event == "import":
            name, filename = str(args[0]), args[1]
            if name.split(".")[0].startswith(_DENIED_MODULES):
                raise ImportError(f"test scripts can't import {name}")
            if filename is not None and not inside(filename, code_roots):
                raise ImportError(f"test scripts can only import the Python standard library, not {name}")
        elif event == "open":
            path, mode, flags = args
            writes = bool(flags & write_flags) if isinstance(flags, int) and flags else \
                any(c in (mode or "") for c in "wax+")
            require(path, write_roots if writes else read_roots, "write" if writes else "read")
        elif event in _WRITE_EVENTS:
            for path in args[:2]:
                if isinstance(path, (str, bytes, os.PathLike)):
                    require(path, write_roots, "change")
        elif event in _LIST_EVENTS:
            require(args[0] if args and args[0] is not None else ".", read_roots, "list")
        elif event == "sqlite3.connect":
            database = os.fsdecode(args[0]) if args and args[0] is not None else ":memory:"
            if database.startswith("file:"):
                raise PermissionError("test scripts can't open SQLite URIs")
            if database != ":memory:":
                require(database, write_roots, "open a database at")

    sys.dont_write_bytecode = True
    sys.addaudithook(guard)


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
    _install_guard(os.environ.get("HOME") or os.getcwd())

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
