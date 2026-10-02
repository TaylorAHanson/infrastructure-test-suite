"""The live Databricks SDK surface, introspected from the installed ``databricks-sdk``.

This is the ground truth both for what a Test script may call through ``w`` and
for what the editor agent grounds on: every ``WorkspaceClient`` service (its
API class), every public method on it, the HTTP verb + REST path the method
issues, its signature, and its docstring. Nothing here is hand-curated, so it
never drifts behind the SDK.

It also owns the two boundary conversions the script runtime needs:
:func:`coerce_kwargs` (JSON kwargs from the sandbox → the SDK's typed
dataclasses/enums) and :func:`to_jsonable` (an SDK response → plain JSON for
the sandbox).
"""
from __future__ import annotations

import dataclasses
import enum
import functools
import inspect
import re
import typing
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.operations import infer_verb, is_blocklisted, Operation

#: WorkspaceClient properties that are plumbing, not services. ``config`` and
#: ``api_client`` carry credentials, so they must never be reachable from a script.
_EXCLUDED_SERVICES = frozenset({"api_client", "config", "dbutils"})
_SERVICE_MODULE_PREFIXES = ("databricks.sdk.service.", "databricks.sdk.mixins.")
_DO_RE = re.compile(r"""self\._api\.do\(\s*['"](\w+)['"]\s*,\s*f?['"]([^'"]+)['"]""")
#: How many items of an iterator response are materialised for the sandbox.
MAX_LIST_ITEMS = 2000


@dataclass(frozen=True)
class SdkMethod:
    service: str
    method: str
    verb: str
    path: str = ""
    signature: str = ""
    summary: str = ""
    doc: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.service}.{self.method}"

    def to_dict(self, *, full: bool = False) -> dict:
        out = {"service": self.service, "method": self.method, "verb": verb_for(self.service, self.method),
               "rest_verb": self.verb, "path": self.path, "summary": self.summary,
               "tier": tier_for(self.service, self.method),
               "call": f"w.{self.service}.{self.method}{self.signature}"}
        if full:
            out["doc"] = self.doc
        return out


@dataclass
class SdkService:
    name: str
    api_class: str
    module: str
    summary: str = ""
    methods: dict[str, SdkMethod] = field(default_factory=dict)


def _first_line(doc: Optional[str]) -> str:
    for line in (doc or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def _short_signature(fn) -> str:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return "(...)"
    params = [p for name, p in sig.parameters.items() if name != "self"]
    parts = []
    kw_marker = False
    for p in params:
        if p.kind == p.KEYWORD_ONLY and not kw_marker:
            parts.append("*")
            kw_marker = True
        ann = p.annotation
        ann_s = ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))
        ann_s = str(ann_s).replace("typing.", "")
        piece = f"{p.name}: {ann_s}" if ann is not p.empty else p.name
        if p.default is not p.empty:
            piece += " = None" if p.default is None else f" = {p.default!r}"
        parts.append(piece)
    ret = sig.return_annotation
    ret_s = "" if ret is sig.empty else f" -> {ret if isinstance(ret, str) else getattr(ret, '__name__', ret)}"
    return f"({', '.join(parts)}){ret_s}"


def _verb_and_path(fn, method_name: str) -> tuple[str, str]:
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        src = ""
    found = _DO_RE.findall(src)
    if found:
        verb, path = found[0]
        return verb.upper(), path
    return infer_verb(method_name), ""


@functools.lru_cache(maxsize=1)
def catalog() -> dict[str, SdkService]:
    """Every WorkspaceClient service and its public methods (cached per process)."""
    from databricks.sdk import WorkspaceClient

    services: dict[str, SdkService] = {}
    for name, attr in inspect.getmembers(WorkspaceClient):
        if name.startswith("_") or name in _EXCLUDED_SERVICES or not isinstance(attr, property):
            continue
        try:
            api_cls = typing.get_type_hints(attr.fget).get("return")
        except Exception:  # noqa: BLE001 - unresolvable hint → not a service
            continue
        module = getattr(api_cls, "__module__", "") or ""
        if not inspect.isclass(api_cls) or not module.startswith(_SERVICE_MODULE_PREFIXES):
            continue
        svc = SdkService(name=name, api_class=api_cls.__name__, module=module,
                         summary=_first_line(api_cls.__doc__))
        for mname, fn in inspect.getmembers(api_cls, predicate=inspect.isfunction):
            if mname.startswith("_"):
                continue
            verb, path = _verb_and_path(fn, mname)
            svc.methods[mname] = SdkMethod(
                service=name, method=mname, verb=verb, path=path,
                signature=_short_signature(fn), summary=_first_line(fn.__doc__),
                doc=inspect.cleandoc(fn.__doc__ or ""),
            )
        services[name] = svc
    return services


def get_method(service: str, method: str) -> Optional[SdkMethod]:
    svc = catalog().get(service)
    return svc.methods.get(method) if svc else None


#: Method-name prefixes that read even when the REST verb is POST (search, query…).
_READ_PREFIXES = ("list", "get", "read", "describe", "search", "query", "fetch", "show")
_WRITE_WORDS = ("create", "update", "delete", "set", "put", "patch", "remove", "reset")
_DESTRUCTIVE_WORDS = ("delete", "remove", "destroy", "terminate", "purge", "wipe", "uninstall")


def verb_for(service: str, method: str) -> str:
    """The verb the safety gate weighs: the REST verb, refined by the method name.

    Many destructive endpoints are POSTs (``clusters/permanent-delete``) and many
    reads are POSTs (``vector_search_indexes/query``), so the name decides.
    """
    m = get_method(service, method)
    verb = m.verb if m else infer_verb(method)
    name = method.lower()
    if any(w in name for w in _DESTRUCTIVE_WORDS):
        return "DELETE"
    if verb == "POST" and name.startswith(_READ_PREFIXES) and not any(w in name for w in _WRITE_WORDS):
        return "GET"
    return verb


def tier_for(service: str, method: str) -> str:
    from app.services.risk import derive_risk_tier

    return derive_risk_tier(operation_for(service, method)).value


def operation_for(service: str, method: str) -> Operation:
    return Operation(service=service, method=method, verb=verb_for(service, method))


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 1]


def search(query: str, limit: int = 12) -> list[dict]:
    """Rank SDK methods against a free-text query (blocklisted calls never returned)."""
    terms = _tokens(query)
    if not terms:
        return []
    scored: list[tuple[float, SdkMethod]] = []
    for svc in catalog().values():
        svc_text = f"{svc.name} {svc.api_class} {svc.summary}".lower()
        for m in svc.methods.values():
            name_text = f"{m.service} {m.method} {m.path}".lower().replace("_", " ")
            hay = f"{name_text} {m.summary.lower()} {svc_text}"
            score = 0.0
            for t in terms:
                if t in name_text:
                    score += 3
                elif t in hay:
                    score += 1
            if score:
                scored.append((score, m))
    scored.sort(key=lambda x: (-x[0], x[1].qualified))
    out = []
    for _, m in scored:
        if is_blocklisted(operation_for(m.service, m.method))[0]:
            continue
        out.append(m.to_dict())
        if len(out) >= limit:
            break
    return out


def describe(service: str, method: Optional[str] = None) -> dict:
    """Full detail for one method, or a service's method index."""
    svc = catalog().get(service)
    if svc is None:
        close = [s for s in catalog() if service.lower().replace("-", "_") in s][:10]
        return {"error": f"no SDK service '{service}'", "did_you_mean": close}
    if not method:
        return {"service": svc.name, "api_class": svc.api_class, "summary": svc.summary,
                "methods": [m.to_dict() for m in svc.methods.values()]}
    m = svc.methods.get(method)
    if m is None:
        return {"error": f"no method '{service}.{method}'", "methods": sorted(svc.methods)}
    out = m.to_dict(full=True)
    blocked, reason = is_blocklisted(operation_for(service, method))
    out["blocklisted"] = blocked
    if blocked:
        out["blocklist_reason"] = reason
    return out


# ---------------------------------------------------------------------------
# Boundary conversions (sandbox JSON ⇄ SDK types)
# ---------------------------------------------------------------------------
def _unwrap_optional(tp: Any) -> Any:
    if typing.get_origin(tp) is typing.Union:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp


def _coerce(value: Any, tp: Any) -> Any:
    tp = _unwrap_optional(tp)
    if value is None:
        return None
    origin = typing.get_origin(tp)
    if origin in (list, typing.List) and isinstance(value, list):
        (inner,) = typing.get_args(tp) or (Any,)
        return [_coerce(v, inner) for v in value]
    if origin in (dict, typing.Dict) and isinstance(value, dict):
        args = typing.get_args(tp)
        inner = args[1] if len(args) == 2 else Any
        return {k: _coerce(v, inner) for k, v in value.items()}
    if inspect.isclass(tp):
        if issubclass(tp, enum.Enum) and isinstance(value, str):
            try:
                return tp(value)
            except ValueError:
                return tp[value]
        if dataclasses.is_dataclass(tp) and isinstance(value, dict) and hasattr(tp, "from_dict"):
            return tp.from_dict(value)
    return value


def coerce_kwargs(fn, kwargs: dict) -> dict:
    """Turn JSON kwargs into the SDK's typed arguments (dataclasses, enums, lists)."""
    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001 - leave as-is when hints can't resolve
        return dict(kwargs)
    return {k: _coerce(v, hints.get(k, Any)) for k, v in kwargs.items()}


def _is_wait(value: Any) -> bool:
    try:
        from databricks.sdk.service._internal import Wait
    except ImportError:  # pragma: no cover - sdk is a hard dependency
        return False
    return isinstance(value, Wait)


def to_jsonable(value: Any, _depth: int = 0) -> Any:
    """An SDK response as plain JSON (iterators materialised, capped)."""
    if _depth > 8:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if _is_wait(value):
        # Wait.__getattr__ raises KeyError (not AttributeError), so hasattr() on it raises.
        out = to_jsonable(value.__dict__.get("response"), _depth + 1)
        bound = value.__dict__.get("_bind") or {}
        if isinstance(out, dict):
            return {**{k: to_jsonable(v, _depth + 1) for k, v in bound.items()}, **out}
        return out
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            return value.as_dict()
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v, _depth + 1) for v in value]
    if hasattr(value, "read") and callable(value.read):
        data = value.read()
        return to_jsonable(data, _depth + 1)
    if hasattr(value, "__iter__"):
        items = []
        for i, item in enumerate(value):
            if i >= MAX_LIST_ITEMS:
                break
            items.append(to_jsonable(item, _depth + 1))
        return items
    return str(value)
