"""The canonical stage spine — as **data**, not a closed enum (ADR-0001).

Every Check result maps onto one shared, ordered vocabulary of stages:

    RESOLVE → CONNECT → TLS → AUTHENTICATE → AUTHORIZE → OPERATE → VERIFY

A handler *maps onto* the spine and **skips** the stages it lacks; it never
invents a private stage. The old model baked these into a ``StepKind`` enum and
the base executor; here they are a registry so that adding a stage is a config
entry, not an enum edit + engine change.

**Connectable vs functional is a derived view** (never a stored primitive): a
failure at or before ``AUTHORIZE`` is a connectivity problem; a failure at
``OPERATE``/``VERIFY`` is a functional one. :func:`derive_connectivity` computes
that from stage outcomes at read time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from app.core.enums import ProbeOutcome


@dataclass(frozen=True)
class Stage:
    """One entry on the canonical spine."""

    id: str
    label: str
    order: int
    #: True while a failure here is a *connectivity* problem (≤ AUTHORIZE).
    connectivity: bool


# The seeded spine. New stages are added here (or via :func:`register_stage`) —
# no enum or base-executor edit required.
_CANONICAL: list[Stage] = [
    Stage("RESOLVE", "Resolve", 10, connectivity=True),
    Stage("CONNECT", "Connect", 20, connectivity=True),
    Stage("TLS", "TLS", 30, connectivity=True),
    Stage("AUTHENTICATE", "Authenticate", 40, connectivity=True),
    Stage("AUTHORIZE", "Authorize", 50, connectivity=True),
    Stage("OPERATE", "Operate", 60, connectivity=False),
    Stage("VERIFY", "Verify", 70, connectivity=False),
]

_REGISTRY: dict[str, Stage] = {s.id: s for s in _CANONICAL}


def register_stage(stage: Stage) -> Stage:
    """Add (or replace) a stage on the spine. Config, not code migration."""
    _REGISTRY[stage.id] = stage
    return stage


def ordered_stages() -> list[Stage]:
    """The spine in canonical order."""
    return sorted(_REGISTRY.values(), key=lambda s: s.order)


def stage_ids() -> list[str]:
    return [s.id for s in ordered_stages()]


def get_stage(stage_id: str) -> Optional[Stage]:
    return _REGISTRY.get(stage_id)


def is_stage(stage_id: str) -> bool:
    return stage_id in _REGISTRY


def connectivity_stage_ids() -> list[str]:
    return [s.id for s in ordered_stages() if s.connectivity]


def functional_stage_ids() -> list[str]:
    return [s.id for s in ordered_stages() if not s.connectivity]


def is_connectivity_stage(stage_id: str) -> bool:
    stage = _REGISTRY.get(stage_id)
    return bool(stage and stage.connectivity)


# ---------------------------------------------------------------------------
# Derived connectable / functional view
# ---------------------------------------------------------------------------
def _outcome_of(item: object) -> Optional[ProbeOutcome]:
    """Read a stage outcome off a StageOutcome row, a StageResult, or a dict."""
    value = getattr(item, "outcome", None)
    if value is None and isinstance(item, dict):
        value = item.get("outcome")
    if value is None:
        return None
    if isinstance(value, ProbeOutcome):
        return value
    return ProbeOutcome(value)


def _stage_of(item: object) -> Optional[str]:
    value = getattr(item, "stage", None)
    if value is None and isinstance(item, dict):
        value = item.get("stage")
    return value


def derive_connectivity(stage_outcomes: Iterable[object]) -> dict:
    """Derive ``{connectable, functional}`` from stage outcomes.

    Pure function over an iterable of stage outcomes (StageOutcome rows,
    StageResults, or plain dicts with ``stage`` + ``outcome``). This is the
    single source of the connectable/functional distinction (CONTEXT.md) — it
    is computed, never stored as an authoritative column.

    * ``connectable`` — ``True`` when every connectivity stage that ran passed,
      ``False`` when any connectivity stage failed, ``None`` when none ran.
    * ``functional``  — ``True`` when a functional stage ran and none failed,
      ``False`` when a functional stage failed, ``None`` when none ran.
    """
    conn_ran = conn_failed = 0
    func_ran = func_failed = 0

    for item in stage_outcomes:
        stage_id = _stage_of(item)
        outcome = _outcome_of(item)
        if stage_id is None or outcome is None or outcome == ProbeOutcome.SKIP:
            continue
        if is_connectivity_stage(stage_id):
            conn_ran += 1
            if outcome.failed:
                conn_failed += 1
        else:
            func_ran += 1
            if outcome.failed:
                func_failed += 1

    connectable: Optional[bool]
    functional: Optional[bool]
    connectable = None if conn_ran == 0 else conn_failed == 0
    functional = None if func_ran == 0 else func_failed == 0
    return {"connectable": connectable, "functional": functional}
