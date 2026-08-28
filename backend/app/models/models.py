"""System-of-record tables (Lakebase Postgres / local SQLite).

The rebuilt domain (ADR-0001, amended by ADR-0017) is three cleanly separated
units plus an extensible, data-driven result model:

* **Check** — the atomic assertion: one open-SDK operation + one expectation.
* **Test**  — a target-agnostic, ordered bundle of Checks (incl. counterfactuals).
* **Suite** — a named selection of Tests for a Run (grouping only).

Results map onto the **canonical stage spine** (a :class:`StageOutcome` row per
stage, keyed by the data-backed stage id — *not* a hardcoded ``StepKind``) and
carry a **failure-taxonomy bucket** (a string id, not a closed enum). The store
is Lakebase-authoritative: edits version in-app (:class:`EntityVersion`) and roll
back in-app; YAML is an export format only. All timestamps are naive UTC.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, Index, Text
from sqlmodel import Field, SQLModel

from app.core.enums import (
    Depth,
    Evaluator,
    Expectation,
    ProbeOutcome,
    RequestSource,
    RiskTier,
    RunStatus,
    SafetyClass,
    TestStatus,
)
from app.core.ids import new_id, utcnow


def _json_col() -> Any:
    return Field(default_factory=dict, sa_column=Column(JSON))


def _json_list() -> Any:
    return Field(default_factory=list, sa_column=Column(JSON))


# ---------------------------------------------------------------------------
# Catalog: Check / Test / Suite
# ---------------------------------------------------------------------------
class Check(SQLModel, table=True):
    """The atomic unit: one operation + its expectation."""

    __tablename__ = "checks"

    id: str = Field(default_factory=lambda: new_id("chk"), primary_key=True)
    display_name: str
    description: str = ""

    # Open-SDK operation: an arbitrary Databricks SDK/API call (ADR-0017),
    # {"service", "method", "verb", "params"}. Ground truth is the SDK surface
    # minus the blocklist — never a curated manifest allowlist.
    operation: dict = _json_col()
    #: Registry key of the handler that performs the operation on the spine.
    handler_key: str = Field(default="databricks_sdk", index=True)

    # Assertion authored in plain text + its compiled form.
    assertion: str = ""
    expectation: Expectation = Field(default=Expectation.PASS)
    evaluator: Evaluator = Field(default=Evaluator.DETERMINISTIC, index=True)
    compiled_predicate: dict = _json_col()
    compilable: bool = Field(default=True)
    #: For a counterfactual: the canonical stage the denial is expected at.
    expected_stage: Optional[str] = Field(default=None)

    # Coarse verb/category-derived tier (ADR-0017); confirmed_risk_tier is the
    # one-confirm operation-specific tier captured only for mutating targets.
    risk_tier: str = Field(default=RiskTier.R0.value, index=True)
    confirmed_risk_tier: Optional[str] = Field(default=None)

    # Optional grounding hints (what remains of the "handler manifest"): the
    # canonical stages the op maps to, counterfactual pair, evidence schema.
    hints: dict = _json_col()

    version: int = Field(default=1)
    enabled: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Test(SQLModel, table=True):
    """A target-agnostic, ordered bundle of Checks (with counterfactuals)."""

    __tablename__ = "tests"

    id: str = Field(default_factory=lambda: new_id("tst"), primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    tags: list[str] = _json_list()
    #: Default identity for the Test: None = the target's own SP; else a
    #: registered role-profile it assumes (ADR-0018; enforcement is Spec 2).
    run_as_role: Optional[str] = Field(default=None)

    #: Test Studio lifecycle (ADR-0006 §8): a ``draft`` never runs until a human
    #: accepts it into ``active``. Defaults to ``active`` so hand-authored and
    #: seeded Tests are runnable; the Assistant's write-path creates ``draft``.
    status: TestStatus = Field(default=TestStatus.ACTIVE, index=True)
    #: How the Test was authored — "human" | "assistant" — for the derivation
    #: panel. The Assistant sets "assistant"; manual/seed paths leave "human".
    origin: str = Field(default="human")
    #: The Assistant's derivation record for an AI-produced Test: chosen SDK
    #: operation, param provenance, grounding example, coarse risk tier, chosen
    #: run-as-role + why, suggested counterfactuals/suite + why. What makes an
    #: AI-produced Test auditable by a stranger (ADR-0006 §8, user story 11).
    derivation: dict = _json_col()

    version: int = Field(default=1)
    enabled: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TestCheck(SQLModel, table=True):
    """Ordered membership of a Check in a Test."""

    __tablename__ = "test_checks"
    __table_args__ = (Index("ix_test_check", "test_id", "check_id", unique=True),)

    id: str = Field(default_factory=lambda: new_id("tc"), primary_key=True)
    test_id: str = Field(foreign_key="tests.id", index=True)
    check_id: str = Field(foreign_key="checks.id", index=True)
    sort_order: int = Field(default=0)
    #: "assertion" (ordinary) or "counterfactual" (expects denial).
    kind: str = Field(default="assertion")
    #: Per-member identity override (lets a counterfactual differ by role,
    #: e.g. "allowed as admin / denied as analyst" — user story 22 / ADR-0018).
    run_as_role: Optional[str] = Field(default=None)
    #: Links a counterfactual to the positive Check it negates.
    counterpart_check_id: Optional[str] = Field(default=None)


class Suite(SQLModel, table=True):
    """A named selection of Tests for a Run (grouping only; no cost logic)."""

    __tablename__ = "suites"

    id: str = Field(default_factory=lambda: new_id("suite"), primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    tags: list[str] = _json_list()
    #: Fail-closed verdict computed from deterministic Checks alone (ADR-0008).
    #: This spec defines the flag; CI-gate enforcement is Spec 3.
    deterministic_only: bool = Field(default=False)
    version: int = Field(default=1)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SuiteMember(SQLModel, table=True):
    """Membership of a Test in a Suite."""

    __tablename__ = "suite_members"
    __table_args__ = (Index("ix_suite_member", "suite_id", "test_id", unique=True),)

    id: str = Field(default_factory=lambda: new_id("sm"), primary_key=True)
    suite_id: str = Field(foreign_key="suites.id", index=True)
    test_id: str = Field(foreign_key="tests.id", index=True)
    sort_order: int = Field(default=0)


class Target(SQLModel, table=True):
    """One Databricks workspace under test (bound to a Test at run time)."""

    __tablename__ = "targets"

    id: str = Field(default_factory=lambda: new_id("tgt"), primary_key=True)
    name: str = Field(index=True)
    workspace_name: Optional[str] = Field(default=None, index=True)
    workspace_id: Optional[str] = Field(default=None)
    host: Optional[str] = Field(default=None)
    #: Authoritative cap on the max risk tier this target will run (ADR-0003).
    safety_class: SafetyClass = Field(default=SafetyClass.READ_ONLY, index=True)
    #: Reference to the target's OAuth M2M SP as a secret-scope ``scope/key``.
    identity_handle: Optional[str] = Field(default=None)
    #: Registered role-profiles this target may assume, each a dict
    #: ``{group_id, label, safety_class}`` capped by the target's safety class
    #: at registration (ADR-0018; see :mod:`app.services.roles`).
    assumable_roles: list[dict] = _json_list()
    tags: dict = _json_col()
    enabled: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# In-app versioning / rollback (Lakebase-authoritative, ADR-0001)
# ---------------------------------------------------------------------------
class EntityVersion(SQLModel, table=True):
    """An immutable snapshot of a Check/Test/Suite for in-app rollback."""

    __tablename__ = "entity_versions"
    __table_args__ = (Index("ix_entity_version", "entity_type", "entity_id", "version"),)

    id: str = Field(default_factory=lambda: new_id("ver"), primary_key=True)
    entity_type: str = Field(index=True)  # "check" | "test" | "suite"
    entity_id: str = Field(index=True)
    version: int = Field(default=1)
    snapshot: dict = _json_col()
    actor: str = Field(default="system")
    created_at: datetime = Field(default_factory=utcnow, index=True)


# ---------------------------------------------------------------------------
# Worked-example library (ADR-0006 §9) — the gold Tests the Assistant grounds on
# ---------------------------------------------------------------------------
class WorkedExample(SQLModel, table=True):
    """A gold worked example the Assistant grounds on via ``search_examples``.

    A **curated seed set ships in the repo** (open-source day-one), and the
    library is **grown by curated suggest-then-confirm promotion** of accepted
    Tests (ADR-0006 §9). Each example pairs a plain-English **intent** with the
    open-SDK operation + assertion it should compile to, so the Assistant can
    retrieve a close prior for a new sentence.
    """

    __tablename__ = "worked_examples"

    id: str = Field(default_factory=lambda: new_id("ex"), primary_key=True)
    #: The plain-English sentence this example teaches ("start a cluster …").
    intent: str = Field(index=True)
    title: str = ""
    description: str = ""
    #: The open-SDK operation the intent compiles to ({service, method, verb, params}).
    operation: dict = _json_col()
    handler_key: str = Field(default="databricks_sdk")
    assertion: str = ""
    expectation: Expectation = Field(default=Expectation.PASS)
    #: A paired counterfactual intent/assertion, when the example teaches one.
    counterfactual: dict = _json_col()
    tags: list[str] = _json_list()
    #: True for the repo-seeded gold set; promoted examples are also curated.
    curated: bool = Field(default=True)
    #: Set when this example was promoted from an accepted Test (provenance).
    source_test_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Execution: request → check runs → stage outcomes
# ---------------------------------------------------------------------------
class RunRequest(SQLModel, table=True):
    """An intent to run a selection of Tests/Suites against targets."""

    __tablename__ = "run_requests"

    id: str = Field(default_factory=lambda: new_id("req"), primary_key=True)
    requested_by: str = Field(default="system")
    request_source: RequestSource = Field(default=RequestSource.API)

    # Selection: empty lists mean "all".
    suite_ids: list[str] = _json_list()
    test_ids: list[str] = _json_list()
    check_ids: list[str] = _json_list()
    target_ids: list[str] = _json_list()
    depth: Depth = Field(default=Depth.QUICK)

    #: An optional per-run safety override (ADR-0004 §5), stored as captured:
    #: ``{target_name, reason, confirmed}``. Honored by the gate only for a human
    #: GUI caller; never for CI/automation.
    override: dict = _json_col()

    status: RunStatus = Field(default=RunStatus.PENDING, index=True)
    locked_by: Optional[str] = None
    locked_at: Optional[datetime] = None

    git_sha: str = "unknown"
    app_version: str = "0.0.0"
    created_at: datetime = Field(default_factory=utcnow, index=True)


class CheckRun(SQLModel, table=True):
    """One Check executed against one Target — the atomic result record.

    connectable/functional are **derived** from the stage outcomes at read time
    (see :func:`app.core.stages.derive_connectivity`) and are never stored here
    as authoritative columns.
    """

    __tablename__ = "check_runs"
    __table_args__ = (Index("ix_check_run_provenance", "target_id", "check_id", "created_at"),)

    run_id: str = Field(default_factory=lambda: new_id("run"), primary_key=True)
    request_id: Optional[str] = Field(default=None, foreign_key="run_requests.id", index=True)
    test_id: Optional[str] = Field(default=None, foreign_key="tests.id", index=True)
    check_id: str = Field(foreign_key="checks.id", index=True)
    target_id: Optional[str] = Field(default=None, foreign_key="targets.id", index=True)

    requested_by: str = "system"
    request_source: RequestSource = Field(default=RequestSource.API)
    depth: Depth = Field(default=Depth.QUICK)

    evaluator: Evaluator = Field(default=Evaluator.DETERMINISTIC, index=True)
    expectation: Expectation = Field(default=Expectation.PASS)
    #: True = this result gates (deterministic); False = advisory (agentic).
    gating: bool = Field(default=True)
    run_as_role: Optional[str] = Field(default=None)
    risk_tier: str = Field(default=RiskTier.R0.value)

    # Verdict + failure classification.
    verdict: RunStatus = Field(default=RunStatus.PENDING, index=True)
    failure_bucket: Optional[str] = Field(default=None, index=True)
    failure_stage: Optional[str] = Field(default=None)
    message: str = ""

    # Context (denormalized for provenance queries).
    workspace_name: Optional[str] = None
    git_sha: str = "unknown"
    app_version: str = "0.0.0"

    evidence: dict = _json_col()

    started_at: Optional[datetime] = Field(default=None, index=True)
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class StageOutcome(SQLModel, table=True):
    """One stage on the canonical spine within a CheckRun.

    Keyed by the data-backed canonical-stage id (a string), not a hardcoded
    ``StepKind`` enum — adding a stage is a config entry (ADR-0001).
    """

    __tablename__ = "stage_outcomes"

    id: str = Field(default_factory=lambda: new_id("stage"), primary_key=True)
    run_id: str = Field(foreign_key="check_runs.run_id", index=True)
    stage: str = Field(index=True)  # canonical stage id, e.g. "AUTHENTICATE"
    sort_order: int = Field(default=0)

    outcome: ProbeOutcome = Field(default=ProbeOutcome.SKIP)
    message: str = ""
    failure_bucket: Optional[str] = Field(default=None, index=True)
    raw_excerpt: Optional[str] = Field(default=None, sa_column=Column(Text))
    evidence: dict = _json_col()

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# Target run-lock: a per-target lease (ADR-0011)
# ---------------------------------------------------------------------------
class TargetLease(SQLModel, table=True):
    """A per-target run lease — the target-level serialization lock (ADR-0011).

    One row per target means at most one active Run holds a mutating target at a
    time (``read-only`` targets never lock, so they never get a row). The holder
    renews ``heartbeat_at`` / ``expires_at`` while alive; a lease is reclaimable
    once it lapses past ``expires_at`` (crash) **or** past ``deadline_at`` (a
    wedged run that exceeds the server-side max run duration). The lock lives in
    the authoritative store (ADR-0001) — no external lock service — so it is
    correct even when runs execute across replicas.
    """

    __tablename__ = "target_leases"

    target_id: str = Field(primary_key=True)
    #: The RunRequest id currently holding the lease.
    holder: str = Field(index=True)
    acquired_at: datetime = Field(default_factory=utcnow)
    heartbeat_at: datetime = Field(default_factory=utcnow)
    #: Reclaimable once ``now >= expires_at`` (heartbeat lapsed → crash).
    expires_at: datetime = Field(default_factory=utcnow, index=True)
    #: Hard cap: reclaimable once ``now >= deadline_at`` even if heartbeating.
    deadline_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Audit (config-plane mutations + version pointers, ADR-0014)
# ---------------------------------------------------------------------------
class AuditEvent(SQLModel, table=True):
    """Append-only record of a config-plane mutation."""

    __tablename__ = "audit_events"

    id: str = Field(default_factory=lambda: new_id("audit"), primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    actor: str = Field(default="system", index=True)
    actor_kind: str = "user"
    action: str = Field(index=True)
    entity_type: str = Field(index=True)
    entity_id: Optional[str] = Field(default=None, index=True)
    summary: str = ""
    before: dict = _json_col()
    after: dict = _json_col()
    request_source: str = "API"
