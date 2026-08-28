"""Assumable role-profiles registered with a Target (ADR-0018).

A Target carries a curated set of **assumable role-profiles**. Each is a
Databricks **group id + label** plus the role's own **safety class** — the
blast-radius profile the role represents. A Test may set a ``run-as-role`` so
its Run's identity is ``(target, assumed-role?)``: the SP by default, or one
registered role the SP assumes via native RBAC ``assume_group`` (ADR-0018).

The **load-bearing control** (ADR-0018 §2, given ADR-0017 made the identity the
*primary* blast-radius bound) is that a role may only be attached to a Target
when the role's own class stays **within the Target's safety class** — a
``read-only`` Target may attach only read-only roles. This keeps the primary
bound intact under assumption: an assumed role can never out-run its Target's
class. Enforced at registration (:func:`validate_roles_for_class`) and again
before dispatch (:func:`resolve_run_as_role`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.enums import SafetyClass


class RoleRegistrationError(ValueError):
    """Raised when an assumable role would out-run its Target's safety class."""


@dataclass(frozen=True)
class RoleProfile:
    """A normalized assumable role-profile: a group id + label + its class."""

    group_id: str
    label: str
    safety_class: SafetyClass

    def to_dict(self) -> dict:
        return {"group_id": self.group_id, "label": self.label,
                "safety_class": self.safety_class.value}

    def matches(self, ref: str) -> bool:
        """Whether a ``run-as-role`` reference names this role (by id or label)."""
        return ref in (self.group_id, self.label)


def normalize_role(raw: object) -> RoleProfile:
    """Coerce a raw role (dict or bare string) into a :class:`RoleProfile`.

    A bare string is treated as ``group_id`` with a ``read-only`` profile — the
    safest default, so an under-specified role can never silently widen a class.
    """
    if isinstance(raw, RoleProfile):
        return raw
    if isinstance(raw, str):
        return RoleProfile(group_id=raw, label=raw, safety_class=SafetyClass.READ_ONLY)
    if isinstance(raw, dict):
        group_id = str(raw.get("group_id") or raw.get("id") or "").strip()
        if not group_id:
            raise RoleRegistrationError("An assumable role needs a group_id.")
        label = str(raw.get("label") or group_id)
        raw_class = raw.get("safety_class", SafetyClass.READ_ONLY.value)
        try:
            safety_class = SafetyClass(raw_class)
        except ValueError as exc:
            raise RoleRegistrationError(f"Unknown role safety_class '{raw_class}'.") from exc
        return RoleProfile(group_id=group_id, label=label, safety_class=safety_class)
    raise RoleRegistrationError(f"Unrecognized role profile: {raw!r}")


def normalize_roles(raw_roles: object) -> list[RoleProfile]:
    return [normalize_role(r) for r in (raw_roles or [])]


def role_within_class(role: RoleProfile, safety_class: SafetyClass) -> bool:
    """True when the role's profile stays within the Target's safety class.

    Uses the same class→tier matrix as the gate: a role may attach only if its
    max tier is no higher than the Target's cap (ADR-0018 §2).
    """
    return role.safety_class.max_risk_tier.order <= safety_class.max_risk_tier.order


def validate_roles_for_class(raw_roles: object, safety_class: SafetyClass) -> list[dict]:
    """Normalize + guard a target's assumable roles against its safety class.

    Returns the roles as stored dicts. Raises :class:`RoleRegistrationError`
    naming the first role that out-runs the class (user story 21) — the guard
    that keeps assumption from widening the primary identity bound.
    """
    roles = normalize_roles(raw_roles)
    for role in roles:
        if not role_within_class(role, safety_class):
            raise RoleRegistrationError(
                f"Role '{role.label}' ({role.safety_class.value}) exceeds the "
                f"target's safety class '{safety_class.value}': a role may not "
                f"out-run its target's blast-radius cap."
            )
    return [r.to_dict() for r in roles]


def find_role(raw_roles: object, ref: str) -> Optional[RoleProfile]:
    """Find a registered role matching a ``run-as-role`` reference, or None."""
    for role in normalize_roles(raw_roles):
        if role.matches(ref):
            return role
    return None
