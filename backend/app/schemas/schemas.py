"""Pydantic request/response schemas for the REST API."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.enums import (
    AuthMode,
    CleanupPolicy,
    ExecutionMode,
    ExecutorKind,
    RequestSource,
    TestCategory,
)


# --------------------------- Test definitions ----------------------------
class TestDefinitionCreate(BaseModel):
    display_name: str
    description: str = ""
    category: TestCategory
    tags: list[str] = Field(default_factory=list)
    target_type: str
    required_capabilities: list[str] = Field(default_factory=list)
    supported_execution_modes: list[ExecutionMode] = Field(default_factory=list)
    executor_kind: ExecutorKind
    executor_key: str
    auth_mode: AuthMode = AuthMode.APP
    enterprise_only: bool = False
    destructive: bool = False
    enabled: bool = True
    timeout_seconds: int = 120
    retries: int = 0
    cleanup_policy: CleanupPolicy = CleanupPolicy.ALWAYS
    parameter_schema: dict = Field(default_factory=dict)
    evidence_schema: dict = Field(default_factory=dict)
    default_parameters: dict = Field(default_factory=dict)


class TestDefinitionUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    enabled: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    retries: Optional[int] = None
    cleanup_policy: Optional[CleanupPolicy] = None
    default_parameters: Optional[dict] = None
    supported_execution_modes: Optional[list[ExecutionMode]] = None


# --------------------------- Suites ----------------------------
class SuiteCreate(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    test_definition_ids: list[str] = Field(default_factory=list)


class SuiteUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    enabled: Optional[bool] = None
    test_definition_ids: Optional[list[str]] = None


# --------------------------- Targets ----------------------------
class TargetCreate(BaseModel):
    name: str
    target_type: str
    catalog: Optional[str] = None
    schema_name: Optional[str] = None
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None
    cloud: str = "aws"
    account_id: Optional[str] = None
    region: Optional[str] = None
    config: dict = Field(default_factory=dict)


# --------------------------- Runs ----------------------------
class RunSubmit(BaseModel):
    """Submit a run request. Empty selection lists mean "all".

    ``idempotency_key`` makes submission safe to retry: re-submitting the same
    key returns the existing request instead of creating a duplicate.
    """

    suite_ids: list[str] = Field(default_factory=list)
    test_definition_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    catalogs: list[str] = Field(default_factory=list)
    execution_mode: ExecutionMode = ExecutionMode.SMOKE
    requested_by: str = "ui-user"
    request_source: RequestSource = RequestSource.UI_MANUAL
    idempotency_key: Optional[str] = None


class RunRequestOut(BaseModel):
    id: str
    status: str
    execution_mode: ExecutionMode
    requested_by: str
    created_at: datetime
    run_ids: list[str] = Field(default_factory=list)


# --------------------------- Schedules ----------------------------
class ScheduleCreate(BaseModel):
    name: str
    cron: str
    timezone: str = "UTC"
    suite_ids: list[str] = Field(default_factory=list)
    test_definition_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    catalogs: list[str] = Field(default_factory=list)
    execution_mode: ExecutionMode = ExecutionMode.SMOKE
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    cron: Optional[str] = None
    timezone: Optional[str] = None
    enabled: Optional[bool] = None
    execution_mode: Optional[ExecutionMode] = None
    suite_ids: Optional[list[str]] = None
    test_definition_ids: Optional[list[str]] = None
    target_ids: Optional[list[str]] = None
    catalogs: Optional[list[str]] = None


# --------------------------- Remote agents ----------------------------
class AgentRegister(BaseModel):
    name: str
    description: str = ""
    vantage_point: str
    capabilities: list[str] = Field(default_factory=list)
    signing_key_id: Optional[str] = None


class AgentResultStep(BaseModel):
    step_kind: str
    outcome: str
    connectivity: str = "UNKNOWN"
    message: str = ""
    error_class: Optional[str] = None
    root_cause_bucket: Optional[str] = None
    recommendation: Optional[str] = None
    raw_payload_excerpt: Optional[str] = None
    evidence: dict = Field(default_factory=dict)


class AgentResultSubmit(BaseModel):
    """Signed results posted back by a remote agent for a claimed run."""

    run_id: str
    agent_id: str
    signature: str  # HMAC-SHA256 over the canonical payload
    steps: list[AgentResultStep] = Field(default_factory=list)
