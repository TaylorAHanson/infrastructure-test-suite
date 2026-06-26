"""Operational system-of-record tables (Lakebase Postgres / local SQLite).

These map 1:1 to the tables in the plan. JSON columns are used for flexible,
metadata-driven fields (tags, schemas, summaries) so adding a new test is
mostly configuration rather than schema change. All timestamps are UTC.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, Index, Text
from sqlmodel import Field, SQLModel

from app.core.enums import (
    AuthMode,
    CleanupPolicy,
    ConnectivityOutcome,
    ExecutionMode,
    ExecutorKind,
    ProbeOutcome,
    RequestSource,
    RootCauseBucket,
    RunStatus,
    StepKind,
    TestCategory,
)
from app.core.ids import new_id, utcnow


def _json_col() -> Any:
    return Field(default_factory=dict, sa_column=Column(JSON))


def _json_list() -> Any:
    return Field(default_factory=list, sa_column=Column(JSON))


# ---------------------------------------------------------------------------
# Catalog: definitions, suites, targets
# ---------------------------------------------------------------------------
class TestDefinition(SQLModel, table=True):
    __tablename__ = "test_definitions"

    id: str = Field(default_factory=lambda: new_id("test"), primary_key=True)
    display_name: str
    description: str = ""
    category: TestCategory = Field(index=True)
    tags: list[str] = _json_list()

    target_type: str = Field(index=True)
    required_capabilities: list[str] = _json_list()
    # Stored as plain strings in JSON (values of ExecutionMode) for clean
    # round-tripping through SQLite/Postgres JSON columns.
    supported_execution_modes: list[str] = _json_list()
    executor_kind: ExecutorKind = Field(index=True)
    # The plugin key the registry resolves to; lets many definitions share one
    # executor differentiated by parameters.
    executor_key: str = Field(index=True)
    auth_mode: AuthMode = Field(default=AuthMode.APP)

    enterprise_only: bool = Field(default=False)
    destructive: bool = Field(default=False)
    enabled: bool = Field(default=True, index=True)

    timeout_seconds: int = Field(default=120)
    retries: int = Field(default=0)
    cleanup_policy: CleanupPolicy = Field(default=CleanupPolicy.ALWAYS)

    parameter_schema: dict = _json_col()
    evidence_schema: dict = _json_col()
    default_parameters: dict = _json_col()

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TestSuite(SQLModel, table=True):
    __tablename__ = "test_suites"

    id: str = Field(default_factory=lambda: new_id("suite"), primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    tags: list[str] = _json_list()
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SuiteMember(SQLModel, table=True):
    __tablename__ = "suite_members"
    __table_args__ = (Index("ix_suite_member", "suite_id", "test_definition_id", unique=True),)

    id: str = Field(default_factory=lambda: new_id("sm"), primary_key=True)
    suite_id: str = Field(foreign_key="test_suites.id", index=True)
    test_definition_id: str = Field(foreign_key="test_definitions.id", index=True)
    sort_order: int = Field(default=0)
    # Optional per-suite parameter overrides for this test.
    parameter_overrides: dict = _json_col()


class TestTarget(SQLModel, table=True):
    """A concrete environment a test can run against (workspace + catalog + ...)."""

    __tablename__ = "test_targets"

    id: str = Field(default_factory=lambda: new_id("tgt"), primary_key=True)
    name: str = Field(index=True)
    # e.g. "catalog", "workspace", "external_system"
    target_type: str = Field(index=True)
    catalog: Optional[str] = Field(default=None, index=True)
    schema_name: Optional[str] = Field(default=None)
    workspace_id: Optional[str] = Field(default=None)
    workspace_name: Optional[str] = Field(default=None)
    cloud: str = Field(default="aws")
    account_id: Optional[str] = None
    region: Optional[str] = None
    # Binding hints (resource keys, connection names) - NOT raw secrets.
    config: dict = _json_col()
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Execution: requests, runs, steps
# ---------------------------------------------------------------------------
class RunRequest(SQLModel, table=True):
    """An intent to execute a selection of tests. The poller turns these into
    one or more :class:`TestRun` rows. Idempotency key dedupes retries."""

    __tablename__ = "run_requests"

    id: str = Field(default_factory=lambda: new_id("req"), primary_key=True)
    idempotency_key: Optional[str] = Field(default=None, index=True, unique=True)
    requested_by: str = Field(default="system")
    request_source: RequestSource = Field(default=RequestSource.UI_MANUAL)

    # Selection: empty list means "all".
    suite_ids: list[str] = _json_list()
    test_definition_ids: list[str] = _json_list()
    target_ids: list[str] = _json_list()
    catalogs: list[str] = _json_list()
    execution_mode: ExecutionMode = Field(default=ExecutionMode.SMOKE)

    schedule_id: Optional[str] = Field(default=None, foreign_key="schedules.id")
    status: RunStatus = Field(default=RunStatus.PENDING, index=True)

    # Poller coordination (claim/lock).
    locked_by: Optional[str] = None
    locked_at: Optional[datetime] = None

    git_sha: str = "unknown"
    app_version: str = "0.0.0"
    created_at: datetime = Field(default_factory=utcnow, index=True)


class TestRun(SQLModel, table=True):
    __tablename__ = "test_runs"

    run_id: str = Field(default_factory=lambda: new_id("run"), primary_key=True)
    request_id: Optional[str] = Field(default=None, foreign_key="run_requests.id", index=True)
    test_definition_id: str = Field(foreign_key="test_definitions.id", index=True)
    target_id: Optional[str] = Field(default=None, foreign_key="test_targets.id", index=True)

    requested_by: str = "system"
    request_source: RequestSource = Field(default=RequestSource.UI_MANUAL)
    execution_mode: ExecutionMode = Field(default=ExecutionMode.SMOKE)

    # Context
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None
    cloud: str = "aws"
    account_id: Optional[str] = None
    region: Optional[str] = None
    catalog: Optional[str] = Field(default=None, index=True)
    git_sha: str = "unknown"
    app_version: str = "0.0.0"
    idempotency_key: Optional[str] = Field(default=None, index=True)

    # Lifecycle
    status: RunStatus = Field(default=RunStatus.PENDING, index=True)
    probe_outcome: Optional[ProbeOutcome] = Field(default=None, index=True)
    connectivity_outcome: Optional[ConnectivityOutcome] = Field(default=None, index=True)
    started_at: Optional[datetime] = Field(default=None, index=True)
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    # Aggregates
    summary_counts: dict = _json_col()       # {PASS: n, FAIL: n, ...}
    connectivity_summary: dict = _json_col()  # {CONNECTABLE: n, ...}
    failure_buckets: list[str] = _json_list()  # normalized RootCauseBucket values
    root_cause_bucket: Optional[RootCauseBucket] = Field(default=None, index=True)
    recommendation: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Links to raw evidence / telemetry
    databricks_job_run_id: Optional[str] = None
    databricks_job_run_url: Optional[str] = None
    telemetry_trace_id: Optional[str] = None
    telemetry_logs_url: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow, index=True)


class TestRunStep(SQLModel, table=True):
    """One probe step within a run (the six-question contract)."""

    __tablename__ = "test_run_steps"

    id: str = Field(default_factory=lambda: new_id("step"), primary_key=True)
    run_id: str = Field(foreign_key="test_runs.run_id", index=True)
    step_kind: StepKind = Field(index=True)
    sort_order: int = Field(default=0)

    outcome: ProbeOutcome = Field(default=ProbeOutcome.SKIP)
    connectivity: ConnectivityOutcome = Field(default=ConnectivityOutcome.UNKNOWN)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    message: str = ""
    error_class: Optional[str] = None
    root_cause_bucket: Optional[RootCauseBucket] = Field(default=None, index=True)
    recommendation: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Truncated request/response excerpt for forensics (never full secrets).
    raw_payload_excerpt: Optional[str] = Field(default=None, sa_column=Column(Text))
    evidence: dict = _json_col()


# ---------------------------------------------------------------------------
# Scheduling, audit, agents, artifacts
# ---------------------------------------------------------------------------
class Schedule(SQLModel, table=True):
    __tablename__ = "schedules"

    id: str = Field(default_factory=lambda: new_id("sched"), primary_key=True)
    name: str = Field(index=True)
    cron: str
    timezone: str = "UTC"
    enabled: bool = Field(default=True, index=True)

    suite_ids: list[str] = _json_list()
    test_definition_ids: list[str] = _json_list()
    target_ids: list[str] = _json_list()
    catalogs: list[str] = _json_list()
    execution_mode: ExecutionMode = Field(default=ExecutionMode.SMOKE)

    # The Databricks Job this schedule is materialised as (created via SDK).
    databricks_job_id: Optional[str] = None
    last_fired_at: Optional[datetime] = None
    created_by: str = "system"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AuditEvent(SQLModel, table=True):
    """Durable audit trail. App logs are ephemeral; these rows are not."""

    __tablename__ = "audit_events"

    id: str = Field(default_factory=lambda: new_id("audit"), primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    actor: str = Field(default="system", index=True)
    actor_kind: str = "user"  # user | service_principal | schedule | agent
    action: str = Field(index=True)  # e.g. suite.update, run.trigger, schedule.create
    entity_type: str = Field(index=True)
    entity_id: Optional[str] = Field(default=None, index=True)
    summary: str = ""
    before: dict = _json_col()
    after: dict = _json_col()
    request_source: RequestSource = Field(default=RequestSource.API)


class RemoteAgent(SQLModel, table=True):
    """A remote executor running in an external vantage point.

    The Databricks App is the control plane; agents poll for work and post
    signed results back. This row stores registration + health, not secrets.
    """

    __tablename__ = "remote_agents"

    id: str = Field(default_factory=lambda: new_id("agent"), primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    # Network vantage point this agent represents (e.g. "corp-network", "onprem-oracle").
    vantage_point: str = Field(index=True)
    # Capabilities/executor_keys this agent can run.
    capabilities: list[str] = _json_list()
    # Key id used to look up the HMAC verification secret (in a secret store).
    signing_key_id: Optional[str] = None
    enabled: bool = Field(default=True)
    last_seen_at: Optional[datetime] = Field(default=None, index=True)
    status: str = "UNKNOWN"  # ONLINE | OFFLINE | UNKNOWN
    created_at: datetime = Field(default_factory=utcnow)


class Artifact(SQLModel, table=True):
    """Pointer to raw evidence stored durably (UC Volume / workspace files)."""

    __tablename__ = "artifacts"

    id: str = Field(default_factory=lambda: new_id("art"), primary_key=True)
    run_id: Optional[str] = Field(default=None, foreign_key="test_runs.run_id", index=True)
    step_id: Optional[str] = Field(default=None, foreign_key="test_run_steps.id")
    kind: str = "evidence"  # evidence | payload | log | screenshot
    content_type: str = "application/json"
    uri: Optional[str] = None        # durable storage location (UC Volume etc.)
    inline_excerpt: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow)
