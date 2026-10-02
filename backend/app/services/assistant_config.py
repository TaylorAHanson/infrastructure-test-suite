"""The assistant's settings: which model it uses and how far one turn may go.

One global row (:class:`AssistantConfig`), edited from Settings → Assistant and
audited like every other config-plane change. :func:`effective` is what callers
read; the model client reads it without a session, so that path is cached for a
few seconds rather than hitting the database on every model call.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

from sqlmodel import Session

from app.core.enums import AuditAction
from app.core.ids import utcnow
from app.models import ASSISTANT_CONFIG_ID, AssistantConfig
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

REASONING_LEVELS = ("off", "low", "medium", "high")
INT_LIMITS = {
    "max_tool_calls": (1, 200),
    "max_output_tokens": (256, 64000),
    "turn_timeout_seconds": (30, 1800),
}
TEMPERATURE_RANGE = (0.0, 2.0)
# The name is interpolated into /serving-endpoints/{name}/invocations.
_ENDPOINT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
FIELDS = ("endpoint", "reasoning", "max_tool_calls", "max_output_tokens", "temperature",
          "turn_timeout_seconds", "allow_live_reads", "allow_dry_run")


@dataclass(frozen=True)
class AssistantSettings:
    endpoint: Optional[str]
    default_endpoint: Optional[str]
    reasoning: str
    max_tool_calls: int
    max_output_tokens: int
    temperature: float
    turn_timeout_seconds: int
    allow_live_reads: bool
    allow_dry_run: bool

    @property
    def model(self) -> Optional[str]:
        """The endpoint a model call goes to: the chosen one, else the deployment default."""
        return self.endpoint or self.default_endpoint

    def to_dict(self) -> dict:
        return {**asdict(self), "model": self.model}


_lock = threading.Lock()
_cached: Optional[tuple[int, float, AssistantSettings]] = None
_CACHE_SECONDS = 10.0


def _deployment_default() -> Optional[str]:
    from app.core.config import settings

    return settings.MODEL_SERVING_ENDPOINT or None


def _from_row(row: Optional[AssistantConfig]) -> AssistantSettings:
    row = row or AssistantConfig()
    return AssistantSettings(
        endpoint=row.endpoint or None, default_endpoint=_deployment_default(),
        reasoning=row.reasoning if row.reasoning in REASONING_LEVELS else "off",
        max_tool_calls=row.max_tool_calls, max_output_tokens=row.max_output_tokens,
        temperature=row.temperature, turn_timeout_seconds=row.turn_timeout_seconds,
        allow_live_reads=row.allow_live_reads, allow_dry_run=row.allow_dry_run,
    )


def _invalidate() -> None:
    global _cached
    with _lock:
        _cached = None


def effective(session: Optional[Session] = None) -> AssistantSettings:
    """The settings in force now (defaults when nothing has been saved)."""
    global _cached
    if session is not None:
        return _from_row(session.get(AssistantConfig, ASSISTANT_CONFIG_ID))

    from app.db.session import get_engine, session_scope

    key, now = id(get_engine()), time.monotonic()
    with _lock:
        if _cached and _cached[0] == key and now - _cached[1] < _CACHE_SECONDS:
            return _cached[2]
    try:
        with session_scope() as s:
            out = _from_row(s.get(AssistantConfig, ASSISTANT_CONFIG_ID))
    except Exception as exc:  # noqa: BLE001 - e.g. the table isn't migrated yet
        logger.warning("assistant settings unavailable, using defaults: %s", exc)
        out = _from_row(None)
    with _lock:
        _cached = (key, now, out)
    return out


def get_config(session: Session) -> AssistantConfig:
    """The single settings row, created with defaults on first read."""
    row = session.get(AssistantConfig, ASSISTANT_CONFIG_ID)
    if row is None:
        row = AssistantConfig(id=ASSISTANT_CONFIG_ID)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _snapshot(row: AssistantConfig) -> dict:
    return {f: getattr(row, f) for f in FIELDS}


def _validated(changes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in changes.items():
        if name not in FIELDS or value is None:
            continue
        if name == "endpoint":
            value = str(value).strip()
            if value and not _ENDPOINT_NAME.match(value):
                raise ValueError("endpoint must be a serving endpoint name (letters, digits, '.', '_', '-')")
            out[name] = value or None
        elif name == "reasoning":
            if value not in REASONING_LEVELS:
                raise ValueError(f"reasoning must be one of {', '.join(REASONING_LEVELS)}")
            out[name] = value
        elif name in INT_LIMITS:
            lo, hi = INT_LIMITS[name]
            if not lo <= int(value) <= hi:
                raise ValueError(f"{name} must be between {lo} and {hi}")
            out[name] = int(value)
        elif name == "temperature":
            lo, hi = TEMPERATURE_RANGE
            if not lo <= float(value) <= hi:
                raise ValueError(f"temperature must be between {lo} and {hi}")
            out[name] = float(value)
        else:
            out[name] = bool(value)
    return out


def update_config(session: Session, *, actor: str, actor_kind: str = "user",
                  request_source: str = "API", **changes: Any) -> AssistantConfig:
    """Apply the given fields (others stay as they are) and audit the change.

    ``endpoint=""`` clears the choice back to the deployment default."""
    fields = _validated(changes)
    row = get_config(session)
    before = _snapshot(row)
    for name, value in fields.items():
        setattr(row, name, value)
    row.updated_by = actor
    row.updated_at = utcnow()
    session.add(row)
    after = _snapshot(row)
    changed = sorted(k for k in FIELDS if before[k] != after[k])
    record_audit(
        session, actor=actor, actor_kind=actor_kind,
        action=AuditAction.ASSISTANT_CONFIG_CHANGE.value,
        entity_type="assistant_config", entity_id=row.id,
        summary=("Assistant settings updated: " + ", ".join(changed)) if changed
        else "Assistant settings saved (no change).",
        before=before, after=after, request_source=request_source, commit=False,
    )
    session.commit()
    session.refresh(row)
    _invalidate()
    return row


# ---------------------------------------------------------------------------
# The models a deployment can pick from
# ---------------------------------------------------------------------------
_models_lock = threading.Lock()
_models_cache: Optional[tuple[float, dict]] = None
_MODELS_CACHE_SECONDS = 60.0


def list_models(refresh: bool = False) -> dict:
    """Chat-capable serving endpoints in this workspace, foundation models first.

    Foundation models are the pay-per-token endpoints serving ``system.ai.*``
    models; custom chat endpoints are listed after them."""
    global _models_cache
    from app.services.run_service import _demo_mode

    if _demo_mode():
        return {"models": [], "error": "demo mode: no workspace is connected"}
    now = time.monotonic()
    with _models_lock:
        if not refresh and _models_cache and now - _models_cache[0] < _MODELS_CACHE_SECONDS:
            return _models_cache[1]
    from app.core.config import get_workspace_client

    try:
        endpoints = list(get_workspace_client().serving_endpoints.list())
    except Exception as exc:  # noqa: BLE001
        logger.warning("listing serving endpoints failed: %s", exc)
        return {"models": [], "error": f"couldn't list serving endpoints: {exc}"}
    models = []
    for e in endpoints:
        if (e.task or "") != "llm/v1/chat":
            continue
        entities = (e.config.served_entities if e.config else None) or []
        entity = entities[0].entity_name if entities else None
        ready = getattr(getattr(e.state, "ready", None), "value", None) == "READY" if e.state else False
        models.append({"name": e.name, "entity": entity, "ready": ready,
                       "foundation": bool(entity and entity.startswith("system.ai."))})
    models.sort(key=lambda m: (not m["foundation"], not m["ready"], m["name"]))
    out = {"models": models, "error": None}
    with _models_lock:
        _models_cache = (now, out)
    return out


_PING_TOOL = {"type": "function", "function": {
    "name": "add", "description": "Add two integers.",
    "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                   "required": ["a", "b"]}}}


def test_model(endpoint: str, reasoning: str = "off") -> dict:
    """One tiny tool-calling request, the same shape the assistant sends.

    Catches endpoints that list as ready but refuse calls (deprecated models) or
    can't call tools at the chosen reasoning level."""
    from app.clients.model_serving import ModelServingError, chat_with_tools

    fields = _validated({"endpoint": endpoint, "reasoning": reasoning})
    started = time.monotonic()
    try:
        reply = chat_with_tools(
            [{"role": "user", "content": "Use the add tool to add 2 and 3."}],
            tools=[_PING_TOOL], tool_choice="auto", endpoint=fields.get("endpoint"),
            max_tokens=512, temperature=0.0, reasoning=fields.get("reasoning", "off"))
    except ModelServingError as exc:
        return {"ok": False, "ms": int((time.monotonic() - started) * 1000), "error": str(exc)[:500]}
    return {"ok": True, "ms": int((time.monotonic() - started) * 1000),
            "called_tools": bool(reply.tool_calls), "reasoning_shown": bool(reply.reasoning),
            "notes": reply.notes}
