"""Core domain enums for the regression validation platform.

These are the canonical vocabulary the whole platform speaks. Every probe
result, run, and step is described in terms of these enums so the UI, the
analytics views, and the remote-agent protocol all agree on meaning.
"""
from __future__ import annotations

from enum import Enum


class ProbeOutcome(str, Enum):
    """Functional outcome of a probe ("does it work?")."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIP = "SKIP"
    PARTIAL = "PARTIAL"


class ConnectivityOutcome(str, Enum):
    """Connectivity outcome of a probe ("is it connectable?").

    Connectable = auth + DNS + routing + handshake + permission precheck.
    Deliberately separate from :class:`ProbeOutcome` so we can answer the two
    questions independently (a target can be CONNECTABLE but still FAIL
    functionally, or NOT_CONNECTABLE which usually short-circuits function).
    """

    CONNECTABLE = "CONNECTABLE"
    NOT_CONNECTABLE = "NOT_CONNECTABLE"
    UNKNOWN = "UNKNOWN"


class ExecutionMode(str, Enum):
    """How aggressively a probe exercises its target."""

    PREFLIGHT = "PREFLIGHT"  # connectable-only: auth + dns + route + permission precheck
    SMOKE = "SMOKE"          # minimal synthetic read/write/query/invoke
    FULL = "FULL"            # full end-to-end validation incl. cleanup


class ExecutorKind(str, Enum):
    """Where/how a probe is executed."""

    DATABRICKS_JOB = "DATABRICKS_JOB"
    APP_INLINE = "APP_INLINE"
    SQL_WAREHOUSE = "SQL_WAREHOUSE"
    MODEL_SERVING = "MODEL_SERVING"
    REMOTE_AGENT = "REMOTE_AGENT"


class TestCategory(str, Enum):
    """High-level grouping aligned to data flow direction."""

    PRODUCER = "PRODUCER"      # inbound: writes data INTO the lakehouse
    PROCESSING = "PROCESSING"  # in-workspace compute / services
    CONSUMER = "CONSUMER"      # outbound: reads data OUT of the lakehouse


class AuthMode(str, Enum):
    """Identity used by a probe when talking to Databricks/targets."""

    APP = "APP"    # app authorization (service principal) - default for shared ops
    USER = "USER"  # user authorization (OBO) - only where per-user governance is needed


class RunStatus(str, Enum):
    """Lifecycle status of a test run."""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class RequestSource(str, Enum):
    """Where a run request originated."""

    UI_MANUAL = "UI_MANUAL"
    SCHEDULE = "SCHEDULE"
    API = "API"
    REMOTE_AGENT = "REMOTE_AGENT"
    CI = "CI"


class CleanupPolicy(str, Enum):
    """How temporary artifacts created by a probe are handled."""

    ALWAYS = "ALWAYS"            # always clean up in a finally block
    ON_SUCCESS = "ON_SUCCESS"    # keep artifacts on failure for forensics
    NEVER = "NEVER"              # probe creates nothing to clean up
    BEST_EFFORT = "BEST_EFFORT"  # try, but never fail the run on cleanup error


class StepKind(str, Enum):
    """The six-question probe contract, expressed as ordered step kinds."""

    PRECHECK = "PRECHECK"      # config/credential/permission precheck
    DNS = "DNS"                # can I resolve the target?
    AUTH = "AUTH"              # can I authenticate?
    NETWORK = "NETWORK"        # can I establish the network path / handshake?
    FUNCTIONAL = "FUNCTIONAL"  # can I perform the minimum useful operation + verify?
    CLEANUP = "CLEANUP"        # can I clean up temporary artifacts?


class RootCauseBucket(str, Enum):
    """Normalized root-cause taxonomy.

    Every failing step is classified into exactly one bucket so analytics can
    group failures meaningfully (especially PrivateLink/NCC failure modes).
    """

    DNS_RESOLUTION_FAILURE = "DNS_RESOLUTION_FAILURE"
    PRIVATE_DNS_MISCONFIGURATION = "PRIVATE_DNS_MISCONFIGURATION"
    NCC_NOT_ATTACHED = "NCC_NOT_ATTACHED"
    NETWORK_POLICY_DENY = "NETWORK_POLICY_DENY"
    PRIVATELINK_ENDPOINT_NOT_ESTABLISHED = "PRIVATELINK_ENDPOINT_NOT_ESTABLISHED"
    TLS_HANDSHAKE_FAILURE = "TLS_HANDSHAKE_FAILURE"
    OAUTH_SCOPE_MISSING = "OAUTH_SCOPE_MISSING"
    APP_RESOURCE_MISSING = "APP_RESOURCE_MISSING"
    SECRET_MISSING = "SECRET_MISSING"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    SERVICE_UNSUPPORTED_IN_REGION = "SERVICE_UNSUPPORTED_IN_REGION"
    FEATURE_NOT_ENABLED = "FEATURE_NOT_ENABLED"
    BUILD_EGRESS_BLOCKED = "BUILD_EGRESS_BLOCKED"
    APP_DEPLOYMENT_FAILURE = "APP_DEPLOYMENT_FAILURE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    DATA_VALIDATION_FAILURE = "DATA_VALIDATION_FAILURE"
    CLEANUP_FAILURE = "CLEANUP_FAILURE"
    EXTERNAL_AGENT_UNAVAILABLE = "EXTERNAL_AGENT_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


# Buckets that indicate a connectivity (vs functional) failure. Used by
# analytics to split "not connectable" from "connectable but broken".
CONNECTIVITY_ROOT_CAUSES = frozenset(
    {
        RootCauseBucket.DNS_RESOLUTION_FAILURE,
        RootCauseBucket.PRIVATE_DNS_MISCONFIGURATION,
        RootCauseBucket.NCC_NOT_ATTACHED,
        RootCauseBucket.NETWORK_POLICY_DENY,
        RootCauseBucket.PRIVATELINK_ENDPOINT_NOT_ESTABLISHED,
        RootCauseBucket.TLS_HANDSHAKE_FAILURE,
        RootCauseBucket.UPSTREAM_TIMEOUT,
        RootCauseBucket.EXTERNAL_AGENT_UNAVAILABLE,
    }
)
