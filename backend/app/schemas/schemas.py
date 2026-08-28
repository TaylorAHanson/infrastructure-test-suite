"""Pydantic request/response schemas for the REST API (Check/Test/Suite/Run)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.enums import Depth, Expectation, RequestSource, SafetyClass


# --------------------------- Operation ----------------------------
class OperationIn(BaseModel):
    """An open-SDK Databricks call (bounded by the blocklist, not an allowlist)."""

    service: str = ""
    method: str = ""
    verb: str = ""
    params: dict = Field(default_factory=dict)


# --------------------------- Checks ----------------------------
class CheckCreate(BaseModel):
    display_name: str
    description: str = ""
    operation: OperationIn = Field(default_factory=OperationIn)
    handler_key: str = "databricks_sdk"
    assertion: str = ""
    #: Optional; when omitted it is inferred from the compiled assertion
    #: (a denial assertion → DENY, otherwise PASS).
    expectation: Optional[Expectation] = None
    expected_stage: Optional[str] = None
    #: Optional one-confirm operation-specific tier (mutating targets only).
    confirmed_risk_tier: Optional[str] = None
    hints: dict = Field(default_factory=dict)
    enabled: bool = True


class CheckUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    operation: Optional[OperationIn] = None
    handler_key: Optional[str] = None
    assertion: Optional[str] = None
    expectation: Optional[Expectation] = None
    expected_stage: Optional[str] = None
    confirmed_risk_tier: Optional[str] = None
    hints: Optional[dict] = None
    enabled: Optional[bool] = None


# --------------------------- Tests ----------------------------
class TestCheckIn(BaseModel):
    check_id: str
    kind: str = "assertion"  # "assertion" | "counterfactual"
    run_as_role: Optional[str] = None
    counterpart_check_id: Optional[str] = None


class TestCreate(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    run_as_role: Optional[str] = None
    #: Ordered membership. A bare list of check ids is also accepted via
    #: ``check_ids`` for convenience (all treated as ordinary assertions).
    members: list[TestCheckIn] = Field(default_factory=list)
    check_ids: list[str] = Field(default_factory=list)


class TestUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    run_as_role: Optional[str] = None
    members: Optional[list[TestCheckIn]] = None
    check_ids: Optional[list[str]] = None
    enabled: Optional[bool] = None


# --------------------------- Suites ----------------------------
class SuiteCreate(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    deterministic_only: bool = False
    test_ids: list[str] = Field(default_factory=list)


class SuiteUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    deterministic_only: Optional[bool] = None
    enabled: Optional[bool] = None
    test_ids: Optional[list[str]] = None


# --------------------------- Targets ----------------------------
class TargetCreate(BaseModel):
    name: str
    workspace_name: Optional[str] = None
    workspace_id: Optional[str] = None
    host: Optional[str] = None
    safety_class: SafetyClass = SafetyClass.READ_ONLY
    identity_handle: Optional[str] = None
    assumable_roles: list[str] = Field(default_factory=list)
    tags: dict = Field(default_factory=dict)


# --------------------------- Runs ----------------------------
class RunSubmit(BaseModel):
    """Submit a run request. Empty selection lists mean "all"."""

    suite_ids: list[str] = Field(default_factory=list)
    test_ids: list[str] = Field(default_factory=list)
    check_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    depth: Depth = Depth.QUICK
    requested_by: str = "ui-user"
    request_source: RequestSource = RequestSource.API
    idempotency_key: Optional[str] = None


class RunRequestOut(BaseModel):
    id: str
    status: str
    depth: Depth
    requested_by: str
    created_at: datetime
    run_ids: list[str] = Field(default_factory=list)


# --------------------------- Assistant / compiler ----------------------------
class CompileRequest(BaseModel):
    assertion: str
