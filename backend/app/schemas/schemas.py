"""Pydantic request/response schemas for the REST API (Check/Test/Suite/Run)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.enums import Depth, RequestSource, SafetyClass


# --------------------------- Tests ----------------------------
class TestCheckIn(BaseModel):
    """One member of a Test: a script check's Check row, in script order."""

    check_id: str
    kind: str = "assertion"  # "assertion" | "counterfactual"
    run_as_role: Optional[str] = None
    counterpart_check_id: Optional[str] = None


class TestUpdate(BaseModel):
    """Edit a Test's fields. Its checks live in its script (``PUT /tests/{id}/script``)."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    run_as_role: Optional[str] = None
    enabled: Optional[bool] = None


class ScriptTestCreate(BaseModel):
    """A new script Test. ``script`` defaults to the starter template."""

    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    run_as_role: Optional[str] = None
    script: Optional[str] = None


class ScriptSave(BaseModel):
    """Save a Test's script (and optionally its fields) as its next version."""

    script: str
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    run_as_role: Optional[str] = None


class ScriptAnalyze(BaseModel):
    script: str


class EditorMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str = ""


class EditorTurnIn(BaseModel):
    """One turn of the test-editor agent: the conversation plus the editor's state."""

    messages: list[EditorMessage]
    script: str = ""
    test_id: Optional[str] = None
    target_id: Optional[str] = None
    last_dry_run: Optional[dict] = None


# --------------------------- Suites ----------------------------
class SuiteCreate(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    test_ids: list[str] = Field(default_factory=list)


class SuiteUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    enabled: Optional[bool] = None
    test_ids: Optional[list[str]] = None


# --------------------------- Targets ----------------------------
class RoleProfileIn(BaseModel):
    """An assumable role-profile registered with a Target (ADR-0018).

    ``safety_class`` is the role's own blast-radius profile; registration
    rejects a role whose class out-runs the Target's (user story 21).
    """

    group_id: str
    label: str = ""
    safety_class: SafetyClass = SafetyClass.READ_ONLY


class TargetCreate(BaseModel):
    name: str
    workspace_name: Optional[str] = None
    workspace_id: Optional[str] = None
    host: Optional[str] = None
    safety_class: SafetyClass = SafetyClass.READ_ONLY
    identity_handle: Optional[str] = None
    assumable_roles: list[RoleProfileIn] = Field(default_factory=list)
    tags: dict = Field(default_factory=dict)


class SafetyCeremonyIn(BaseModel):
    """The friction-ceremony inputs for a safety-gate-defeating edit (ADR-0010).

    Required only when a ``safety_class`` change **widens** the blast radius (a
    downgrade). ``typed_confirmation`` is the AWS-style phrase the human typed;
    ``reason`` is recorded in the audit log. Friction, not permission."""

    typed_confirmation: str = ""
    reason: str = ""


class TargetUpdate(BaseModel):
    """Edit a Target. A ``safety_class`` **downgrade** (wider blast radius)
    requires the friction ceremony (``ceremony``) and is audited with its reason;
    changing ``identity_handle`` re-runs the test-connection guard."""

    name: Optional[str] = None
    workspace_name: Optional[str] = None
    workspace_id: Optional[str] = None
    host: Optional[str] = None
    safety_class: Optional[SafetyClass] = None
    identity_handle: Optional[str] = None
    assumable_roles: Optional[list[RoleProfileIn]] = None
    tags: Optional[dict] = None
    enabled: Optional[bool] = None
    #: The friction ceremony, required on a safety-class downgrade (ADR-0010).
    ceremony: Optional[SafetyCeremonyIn] = None


# --------------------------- Runs ----------------------------
class SafetyOverrideIn(BaseModel):
    """A human, GUI-only authorization to elevate one operation (ADR-0004 §5).

    ``target_name`` is the value the operator typed to confirm; the gate checks
    it against the target and rejects the override for any machine identity.
    """

    target_name: str
    reason: str = ""
    confirmed: bool = True


class ScriptDryRun(BaseModel):
    """Run a script once against a target without recording a Run."""

    script: str
    target_id: str
    depth: Depth = Depth.QUICK
    run_as_role: Optional[str] = None
    override: Optional[SafetyOverrideIn] = None
    params: dict = Field(default_factory=dict)


class RunSubmit(BaseModel):
    """Submit a run request. Empty selection lists mean "all".

    There is **no idempotency key** (ADR-0005 §7 / ADR-0011): a retried submit is
    just another independent run. ``depth`` defaults to ``quick``; ``full`` is an
    explicit opt-in (ADR-0007 §3) — CI never accidentally triggers an expensive
    run.
    """

    suite_ids: list[str] = Field(default_factory=list)
    test_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    depth: Depth = Depth.QUICK
    requested_by: str = "ui-user"
    request_source: RequestSource = RequestSource.API
    #: A non-deploy event reference (e.g. "metastore grant change · 21:14") to pin
    #: on the run header for an Overnight run with no commit (ADR-0015). None for
    #: an ordinary CI-triggered run, which carries the commit sha instead.
    event_ref: Optional[str] = None
    #: An optional per-run safety override (honored only for a human GUI caller).
    override: Optional[SafetyOverrideIn] = None
    #: Run params a Test script reads with ``params`` / ``setting()`` — the same
    #: params a dry run takes. A run param wins over the Target's tag.
    params: dict = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _no_check_selection(cls, data):
        # Ignored, a stale ``check_ids`` would leave the selection empty — "run all".
        if isinstance(data, dict) and data.get("check_ids"):
            raise ValueError("check_ids is no longer a run selection: name the test_ids or suite_ids to run")
        return data


class RunRequestOut(BaseModel):
    """The ``POST /runs`` acknowledgement (ADR-0005 §2).

    ``run_id`` is the id the caller polls with ``GET /runs/{run_id}`` — it is the
    RunRequest id (``id`` is kept as an alias). The fan-out CheckRun ids appear on
    the verdict resource once the run has expanded.
    """

    id: str
    run_id: str
    status: str
    phase: str = "running"
    depth: Depth
    requested_by: str
    created_at: datetime
    run_ids: list[str] = Field(default_factory=list)


# --------------------------- Assistant ----------------------------
class PageContextIn(BaseModel):
    """The context a surface hands the Assistant so its help is page-aware.

    A contract — *what* each page declares (ADR-0012 §2) — not plumbing: the
    Test being edited, the target(s) on screen, or the finding under triage.
    """

    page: str = ""
    target_id: Optional[str] = None
    target_ids: list[str] = Field(default_factory=list)
    test_id: Optional[str] = None
    suite_id: Optional[str] = None
    #: A finding under triage: {"check_id", "target_id"} — routes to RCA.
    finding: Optional[dict] = None


class AssistantTurnIn(BaseModel):
    """One turn to the app-wide Assistant (ADR-0012): a page context + a turn."""

    turn: str = ""
    page_context: PageContextIn = Field(default_factory=PageContextIn)
    #: "auto" lets the harness pick authoring vs RCA from the page context.
    mode: str = "auto"


class RcaRequestIn(BaseModel):
    """Ask the Assistant's read-path to suggest a cause + lane for a finding."""

    check_id: str
    target_id: Optional[str] = None
    turn: str = ""


# --------------------------- Findings feed (ADR-0009, #33) ----------------
class FindingRefOut(BaseModel):
    """A Test or Suite the finding belongs to — enough to deep-link from triage."""

    id: str
    name: str


class FindingOut(BaseModel):
    """One finding — the current failing state of a ``(target, check)`` series.

    A finding is *not* a raw run: it's the series' current state, stating its
    failure as ``bucket @ stage`` and carrying the run that produced it so the
    view can thread provenance from there (#34).
    """

    check_id: str
    check_name: str
    target_id: Optional[str] = None
    workspace_name: Optional[str] = None
    run_id: str
    #: The parent Run request, when the CheckRun was dispatched from one —
    #: so triage can open ``/runs/{request_id}``.
    request_id: Optional[str] = None
    #: Tests that contain this Check. When the CheckRun recorded ``test_id``,
    #: that Test alone; otherwise every Test that lists the Check.
    tests: list[FindingRefOut] = Field(default_factory=list)
    #: Suites that contain those Tests (a Suite is a filter on Tests, not a page).
    suites: list[FindingRefOut] = Field(default_factory=list)
    verdict: str
    failure_bucket: Optional[str] = None
    failure_stage: Optional[str] = None
    #: The ``bucket @ stage`` label (ADR-0001).
    bucket_at_stage: Optional[str] = None
    #: The bucket's human label from the taxonomy ("Unexpected result").
    failure_label: Optional[str] = None
    lane: str
    #: True = a gating result; False = an ``advisory=True`` check's (ADR-0008).
    gating: bool = True
    evaluator: str
    request_source: str
    git_sha: str = "unknown"
    message: str = ""
    #: The last-green → first-bad troubleshooting spine (#34, ADR-0009): keyed to
    #: a commit sha or a non-deploy event ref, honest across the retention edge.
    provenance: Optional[dict] = None
    #: Advisory: recent deterministic verdicts alternate. Never gates (ADR-0008).
    flaky: bool = False
    finished_at: Optional[datetime] = None
    created_at: datetime


# --------------------------- Branding (ADR-0013, #38) --------------------
class BrandingUpdate(BaseModel):
    """A branding edit against the **closed** presentation schema (ADR-0013 §1).

    ``extra="forbid"`` *is* the closed-schema wall at the HTTP boundary: an
    unknown field is rejected with a 422, so branding can never grow a runtime
    "add a setting." Every field is optional — an edit is a partial patch of the
    single global record. ``logo``/``favicon`` are ``data:`` URLs (``data:image/
    png;base64,…`` / SVG); an explicit ``null`` clears the blob.
    """

    model_config = ConfigDict(extra="forbid")

    app_name: Optional[str] = None
    assistant_name: Optional[str] = None
    footer_text: Optional[str] = None
    theme: Optional[dict] = None
    logo: Optional[str] = None
    favicon: Optional[str] = None
    #: Sentinels so a caller can distinguish "leave the blob" from "clear it":
    #: set these true to clear the respective blob (``logo``/``favicon`` = null
    #: is ambiguous with "not provided" under exclude_unset semantics).
    clear_logo: bool = False
    clear_favicon: bool = False


class BrandingOut(BaseModel):
    """The public branding record the shell reads on load + via poll (ADR-0013 §4).

    Presentation fields inline; blobs are **not** inlined (they are served from
    their own routes) — only ``has_logo``/``has_favicon`` flags travel here, so
    the poll payload stays small.
    """

    app_name: str
    assistant_name: str
    footer_text: str
    theme: dict
    has_logo: bool = False
    logo_content_type: Optional[str] = None
    has_favicon: bool = False
    favicon_content_type: Optional[str] = None
    updated_at: datetime
    updated_by: str = "system"


class LaneOut(BaseModel):
    """One lane of the feed: its ranked findings + the collapsed non-actionable count."""

    lane: str
    findings: list[FindingOut] = Field(default_factory=list)
    #: Nominal/recovered series in this lane, collapsed (ADR-0009 §6).
    everything_else: int = 0


class FindingsFeedOut(BaseModel):
    """The two-lane triage feed (Recent-deployments first, then Overnight)."""

    generated_at: datetime
    #: The rolling window (hours) that bounds the Recent-deployments lane.
    window_hours: int
    lanes: list[LaneOut]


# --------------------------- Run-completion webhook --------------------------
class WebhookConfigOut(BaseModel):
    """The run-completion webhook sink config (ADR-0016)."""

    id: str
    enabled: bool
    urls: list[str] = Field(default_factory=list)
    #: The toggle: off = fire only on ≥ 1 finding; on = fire on every completion.
    fire_on_all_completions: bool = False
    updated_by: str = "system"
    updated_at: datetime


class WebhookConfigUpdate(BaseModel):
    """Edit the sink config; only the fields provided are changed.

    Editable by any authenticated human with no ceremony but **audited**
    (ADR-0010 / ADR-0014) — the presentation-only sink can't defeat a gate.
    """

    urls: Optional[list[str]] = None
    enabled: Optional[bool] = None
    fire_on_all_completions: Optional[bool] = None


# --------------------------- Assistant settings ------------------------------
class AssistantConfigOut(BaseModel):
    """The assistant's model and per-turn limits."""

    #: The chosen endpoint; None means the deployment default below.
    endpoint: Optional[str] = None
    #: MODEL_SERVING_ENDPOINT, the deployment's default.
    default_endpoint: Optional[str] = None
    #: The endpoint calls actually go to.
    model: Optional[str] = None
    reasoning: str = "off"
    max_tool_calls: int
    max_output_tokens: int
    temperature: float
    turn_timeout_seconds: int
    allow_live_reads: bool
    allow_dry_run: bool
    updated_by: str = "system"
    updated_at: datetime
    #: The accepted range of each numeric setting, for the form.
    limits: dict[str, list[float]] = Field(default_factory=dict)


class AssistantConfigUpdate(BaseModel):
    """Change the given settings; ``endpoint=""`` goes back to the deployment default."""

    endpoint: Optional[str] = None
    reasoning: Optional[str] = None
    max_tool_calls: Optional[int] = None
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    turn_timeout_seconds: Optional[int] = None
    allow_live_reads: Optional[bool] = None
    allow_dry_run: Optional[bool] = None


class ModelTestIn(BaseModel):
    endpoint: Optional[str] = None
    reasoning: str = "off"
