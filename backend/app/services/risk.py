"""Coarse, verb/category-derived risk tier (ADR-0017 §2c).

Under open-SDK authoring the risk tier is **not** a hand-authored per-operation
manifest value; it is derived coarsely from the operation's verb:

    GET-shaped        → R0 (read)
    POST/PATCH-shaped → R1 (safe mutation)
    DELETE-shaped     → R2 (destructive)
    blocklisted       → R3 (catastrophic — the never-floor)

This is a *secondary* cap: the least-privilege identity is the primary bound
(ADR-0017 §2a), and the blocklist is the R3 floor (§2b). Because a coarse verb
can't tell a benign ``POST`` (create dashboard) from a catastrophic one, a
**one-confirm operation-specific tier** is captured — suggest-then-confirm —
**only** for operations bound for a *mutating* Target (`sandbox`/`prod-for-us`).
``read-only`` Targets need no confirmation: the read-only identity bounds them.
"""
from __future__ import annotations

from typing import Any, Optional

from app.core.enums import RiskTier, SafetyClass
from app.core.operations import Operation, is_blocklisted

_TIER_BY_VERB = {
    "GET": RiskTier.R0,
    "POST": RiskTier.R1,
    "PUT": RiskTier.R1,
    "PATCH": RiskTier.R1,
    "DELETE": RiskTier.R2,
}


def derive_risk_tier(operation: Any) -> RiskTier:
    """Derive the coarse risk tier for an operation (Operation or dict)."""
    op = operation if isinstance(operation, Operation) else Operation.from_dict(operation)
    blocked, _ = is_blocklisted(op)
    if blocked:
        return RiskTier.R3
    return _TIER_BY_VERB.get(op.verb.upper(), RiskTier.R1)


def needs_tier_confirmation(operation: Any, safety_class: Optional[SafetyClass]) -> bool:
    """Whether a one-confirm operation-specific tier should be captured.

    True only for a *mutating* target running a *mutating* op — exactly where a
    coarse verb guess could under-classify a catastrophic call. Read targets and
    read (R0) ops never need it.
    """
    if safety_class is None or not safety_class.is_mutating:
        return False
    return derive_risk_tier(operation) != RiskTier.R0


def effective_risk_tier(operation: Any, confirmed_tier: Optional[RiskTier]) -> RiskTier:
    """The tier the gate should use: a human-confirmed tier wins over the guess.

    The confirmed tier (captured suggest-then-confirm for mutating targets) can
    only *raise* the coarse guess, never lower it below the derived floor — a
    human confirming can catch a catastrophic ``POST`` the verb missed, but
    can't wave a delete through as a read.
    """
    coarse = derive_risk_tier(operation)
    if confirmed_tier is None:
        return coarse
    return confirmed_tier if confirmed_tier.order >= coarse.order else coarse
