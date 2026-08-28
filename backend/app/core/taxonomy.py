"""The failure taxonomy — an **extensible** classification, not a closed enum.

ADR-0001 requires the failure classification to be an extensible taxonomy over
the stage spine: a new root-cause bucket must be introducible without a code
migration to an enum. So buckets live in a registry keyed by string id; a
Check result records its bucket as a string, and the taxonomy answers "is this
a connectivity bucket?" and "what stage does it usually surface at?".

A curated seed set ships in-repo (below). ``register_bucket`` adds more at
runtime / from config.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FailureBucket:
    """One entry in the failure taxonomy."""

    id: str
    label: str
    #: True when a failure in this bucket is a *connectivity* failure.
    connectivity: bool
    #: The canonical stage this bucket typically surfaces at (hint only).
    stage: Optional[str] = None


# Seeded taxonomy. New buckets are added here or via ``register_bucket`` — never
# by editing a closed enum. ``connectivity`` splits "not connectable" from
# "connectable but broken"; ``stage`` is the canonical spine stage it maps to.
_SEED: list[FailureBucket] = [
    FailureBucket("DNS_RESOLUTION_FAILURE", "DNS resolution failure", True, "RESOLVE"),
    FailureBucket("PRIVATE_DNS_MISCONFIGURATION", "Private DNS misconfiguration", True, "RESOLVE"),
    FailureBucket("NCC_NOT_ATTACHED", "NCC not attached", True, "CONNECT"),
    FailureBucket("NETWORK_POLICY_DENY", "Network policy deny", True, "CONNECT"),
    FailureBucket("PRIVATELINK_ENDPOINT_NOT_ESTABLISHED", "PrivateLink endpoint not established", True, "CONNECT"),
    FailureBucket("TLS_HANDSHAKE_FAILURE", "TLS handshake failure", True, "TLS"),
    FailureBucket("AUTHENTICATION_FAILURE", "Authentication failure", True, "AUTHENTICATE"),
    FailureBucket("OAUTH_SCOPE_MISSING", "OAuth scope missing", True, "AUTHENTICATE"),
    FailureBucket("SECRET_MISSING", "Secret missing", True, "AUTHENTICATE"),
    FailureBucket("PERMISSION_DENIED", "Permission denied", True, "AUTHORIZE"),
    FailureBucket("APP_RESOURCE_MISSING", "App resource missing", True, "AUTHORIZE"),
    FailureBucket("TARGET_NOT_FOUND", "Target not found", True, "RESOLVE"),
    FailureBucket("UPSTREAM_TIMEOUT", "Upstream timeout", True, "CONNECT"),
    # Functional (connectable, but the operation itself failed).
    FailureBucket("OPERATION_FAILED", "Operation failed", False, "OPERATE"),
    FailureBucket("DATA_VALIDATION_FAILURE", "Data validation failure", False, "VERIFY"),
    FailureBucket("UNEXPECTED_RESULT", "Unexpected result", False, "VERIFY"),
    FailureBucket("FEATURE_NOT_ENABLED", "Feature not enabled", False, "OPERATE"),
    FailureBucket("SERVICE_UNSUPPORTED", "Service unsupported", False, "OPERATE"),
    FailureBucket("CLEANUP_FAILURE", "Cleanup failure", False, "VERIFY"),
    # Safety / structural.
    FailureBucket("BLOCKED_BY_SAFETY", "Blocked by safety gate", False, "AUTHORIZE"),
    FailureBucket("COUNTERFACTUAL_NOT_DENIED", "Counterfactual was not denied", False, "AUTHORIZE"),
    FailureBucket("UNKNOWN", "Unknown", False, None),
]

_REGISTRY: dict[str, FailureBucket] = {b.id: b for b in _SEED}


def register_bucket(bucket: FailureBucket) -> FailureBucket:
    """Add (or replace) a failure bucket. Config, not enum migration."""
    _REGISTRY[bucket.id] = bucket
    return bucket


def get_bucket(bucket_id: str) -> Optional[FailureBucket]:
    return _REGISTRY.get(bucket_id)


def all_buckets() -> list[FailureBucket]:
    return list(_REGISTRY.values())


def bucket_ids() -> list[str]:
    return list(_REGISTRY.keys())


def is_bucket(bucket_id: str) -> bool:
    return bucket_id in _REGISTRY


def is_connectivity_bucket(bucket_id: Optional[str]) -> bool:
    bucket = _REGISTRY.get(bucket_id or "")
    return bool(bucket and bucket.connectivity)


def bucket_at_stage(bucket_id: Optional[str], stage: Optional[str]) -> str:
    """Render the ``bucket @ stage`` label the triage views use (ADR-0009)."""
    b = bucket_id or "UNKNOWN"
    return f"{b} @ {stage}" if stage else b
