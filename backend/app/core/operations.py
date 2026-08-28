"""The open-SDK operation model + the R3-never blocklist (ADR-0017).

A Check expresses its operation as an **arbitrary Databricks SDK/API call**,
*not* a curated per-operation manifest entry. The ground truth for what may be
expressed is therefore "the SDK/OpenAPI surface **minus the blocklist**" — the
blocklist is a denylist of the catastrophic, and it is the global **R3-never
floor** enforced regardless of identity or target.

An :class:`Operation` is a small normalized descriptor of that call:

    {"service": "clusters", "method": "delete", "verb": "DELETE", "params": {...}}

``verb`` is a coarse HTTP-ish verb; when absent it is inferred from the method
name (``list``/``get`` → GET, ``create`` → POST, ``delete`` → DELETE, ...). The
verb is what the coarse risk-tier derivation reads (see
:mod:`app.services.risk`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Method-name prefixes → coarse verb. Order matters (longest/most-specific win).
_VERB_BY_PREFIX: list[tuple[tuple[str, ...], str]] = [
    (("list", "get", "read", "describe", "show", "fetch", "query", "search", "export", "download"), "GET"),
    (("create", "add", "put", "upload", "start", "restart", "submit", "run", "invoke", "execute", "post"), "POST"),
    (("update", "edit", "patch", "set", "assign", "reset", "rotate", "enable", "disable"), "PATCH"),
    (("delete", "remove", "destroy", "purge", "drop", "revoke", "cancel", "terminate", "uninstall"), "DELETE"),
]


def infer_verb(method: str) -> str:
    """Infer a coarse HTTP-ish verb from an SDK method name."""
    name = (method or "").lower()
    for prefixes, verb in _VERB_BY_PREFIX:
        if any(name == p or name.startswith(p + "_") or name.startswith(p) for p in prefixes):
            return verb
    return "POST"  # unknown mutating-shaped call → treat as a write, not a read


@dataclass
class Operation:
    """A normalized Databricks SDK/API call a Check performs."""

    service: str = ""
    method: str = ""
    verb: str = ""
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.verb:
            self.verb = infer_verb(self.method)
        self.verb = self.verb.upper()

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Operation":
        data = data or {}
        return cls(
            service=str(data.get("service", "")),
            method=str(data.get("method", "")),
            verb=str(data.get("verb", "")),
            params=dict(data.get("params", {}) or {}),
        )

    def to_dict(self) -> dict:
        return {"service": self.service, "method": self.method, "verb": self.verb, "params": self.params}

    @property
    def qualified(self) -> str:
        return f"{self.service}.{self.method}".strip(".")


# ---------------------------------------------------------------------------
# The blocklist — the global R3-never floor (a maintained, in-repo data artifact)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BlocklistRule:
    """One catastrophic pattern that no Check may ever express."""

    id: str
    reason: str
    #: Match when the operation's ``service`` contains any of these (empty = any).
    services: tuple[str, ...] = ()
    #: Match when the operation's ``method`` contains any of these substrings.
    method_contains: tuple[str, ...] = ()


# The catastrophic (R3) set. This is deliberately small: identity (least-priv
# SP / assumed role) and the coarse tier handle everything else (ADR-0017 §2);
# the blocklist only has to catch the truly unrecoverable.
BLOCKLIST: list[BlocklistRule] = [
    BlocklistRule("delete-workspace", "Deleting a workspace is catastrophic and irreversible.",
                  services=("workspaces", "workspace"), method_contains=("delete", "destroy")),
    BlocklistRule("delete-account", "Account-level deletion is catastrophic.",
                  services=("account",), method_contains=("delete", "destroy")),
    BlocklistRule("delete-metastore", "Deleting a Unity Catalog metastore destroys all governed data.",
                  services=("metastores", "metastore"), method_contains=("delete",)),
    BlocklistRule("force-delete-catalog", "Force-deleting a catalog cascades to every schema and table.",
                  services=("catalogs", "catalog"), method_contains=("delete",)),
    BlocklistRule("delete-storage-credential", "Removing a storage credential can orphan all governed storage.",
                  services=("storage_credentials", "storage_credential"), method_contains=("delete",)),
    BlocklistRule("purge", "Bulk purge operations are unrecoverable.",
                  method_contains=("purge", "wipe")),
]


def _rule_matches(rule: BlocklistRule, op: Operation) -> bool:
    service = op.service.lower()
    method = op.method.lower()
    if rule.services and not any(s in service for s in rule.services):
        return False
    if rule.method_contains and not any(m in method for m in rule.method_contains):
        return False
    # A rule with neither constraint would match everything — guard against it.
    return bool(rule.services or rule.method_contains)


def is_blocklisted(operation: Any) -> tuple[bool, Optional[str]]:
    """Return ``(blocked, reason)`` for an operation (Operation or dict).

    This is the R3-never floor: a blocklisted operation can never be expressed
    by any Check, regardless of identity or target.
    """
    op = operation if isinstance(operation, Operation) else Operation.from_dict(operation)
    for rule in BLOCKLIST:
        if _rule_matches(rule, op):
            return True, rule.reason
    return False, None
