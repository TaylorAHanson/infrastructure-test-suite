"""Coarse, verb/category-derived risk tier (ADR-0017 §2c).

The risk tier is **not** a hand-authored per-operation manifest value; it is
derived coarsely from the operation's verb:

    GET-shaped        → R0 (read)
    POST/PATCH-shaped → R1 (safe mutation)
    DELETE-shaped     → R2 (destructive)
    blocklisted       → R3 (catastrophic — the never-floor)

This is a *secondary* cap: the least-privilege identity is the primary bound
(ADR-0017 §2a), and the blocklist is the R3 floor (§2b).
"""
from __future__ import annotations

from typing import Any

from app.core.enums import RiskTier
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
