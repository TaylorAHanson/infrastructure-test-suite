// Shared TypeScript types mirroring the backend Pydantic/SQLModel schemas —
// the Check / Test / Suite / Run model (ADR-0001/0005/0007/0017). Every Test is
// a Python script; a Check is the result of one `with check(…)` block in it.

// ---------------- Small closed vocab (mirrors app.core.enums) ----------------
export type Depth = 'quick' | 'full'
/** Coarse lifecycle phase for the async poll contract (ADR-0005/0011). */
export type Phase = 'queued' | 'running' | 'terminal'
/** `DENY` marks a counterfactual: a `with expect_deny(…)` block. */
export type Expectation = 'PASS' | 'DENY'
/** Outcome of a single stage on the canonical spine. */
export type StageOutcomeStatus = 'PASS' | 'FAIL' | 'SKIP' | 'ERROR'
/** A Run's lifecycle + terminal verdict (RunStatus). Only PASS gates a pipeline. */
export type Verdict =
  | 'PENDING'
  | 'QUEUED'
  | 'RUNNING'
  | 'PASS'
  | 'FAIL'
  | 'ERROR'
  | 'BLOCKED_BY_SAFETY'
  | 'SKIPPED'
  | 'CANCELLED'

/** The canonical stage spine, in order (RESOLVE → … → VERIFY). A check skips
 *  stages it never reaches; failure ≤ AUTHORIZE is connectivity, OPERATE/VERIFY
 *  is functional (derived, never stored). */
export const CANONICAL_STAGES = [
  'RESOLVE',
  'CONNECT',
  'TLS',
  'AUTHENTICATE',
  'AUTHORIZE',
  'OPERATE',
  'VERIFY',
] as const

// ---------------- Catalog: Check / Test / Suite ----------------
/** One `with check(…)` / `with expect_deny(…)` block of a Test script, as the
 *  read-only `GET /checks` serves it. Its id follows the block's name, so run
 *  history survives edits. */
export interface Check {
  id: string
  display_name: string
  description: string
  expectation: Expectation
  expected_stage?: string | null
  /** The highest risk tier among the block's calls. */
  risk_tier: string
  confirmed_risk_tier?: string | null
  /** `calls` (the ops the block makes), `advisory`, `role`. */
  hints?: Record<string, unknown>
  version: number
  enabled: boolean
  created_at?: string
  updated_at?: string
}

/** A named selection of Tests for a Run (grouping only; ADR-0007). */
export interface Suite {
  id: string
  name: string
  description: string
  tags: string[]
  version: number
  enabled: boolean
  test_ids: string[]
  created_at?: string
  updated_at?: string
}

// ---------------- Run API (rebuilt, ADR-0005/0011) ----------------
/** A row from `GET /runs/requests` — the raw RunRequest. Once terminal, `status`
 *  holds the rolled-up fail-closed verdict; `phase` is derived from it. */
export interface RunRequestSummary {
  id: string
  requested_by: string
  request_source: string
  suite_ids: string[]
  test_ids: string[]
  target_ids: string[]
  depth: Depth
  status: Verdict
  override: Record<string, unknown>
  git_sha: string
  app_version: string
  created_at: string
}

/** The `POST /runs` acknowledgement (ADR-0005 §2). */
export interface RunRequestOut {
  id: string
  run_id: string
  status: string
  phase: string
  depth: string
  requested_by: string
  created_at: string
  run_ids: string[]
}

export interface FailureSummary {
  gating: Record<string, number>
  advisory: Record<string, number>
}

/** The one shared verdict resource for a Run (`GET /runs/{req_id}`, ADR-0005 §5). */
export interface VerdictResource extends RunRequestSummary {
  run_id: string
  run_ids: string[]
  phase: Phase
  verdict: Verdict
  /** {test_id | "(ad-hoc)": verdict} over each Test's gating CheckRuns. */
  test_rollup: Record<string, Verdict>
  failure_summary: FailureSummary
  /** Derived connectable/functional aggregate (null when there are no gating runs). */
  connectable: boolean | null
  functional: boolean | null
}

/** One stage on the canonical spine within a CheckRun. */
export interface StageOutcome {
  id: string
  run_id: string
  stage: string
  sort_order: number
  outcome: StageOutcomeStatus
  message: string
  failure_bucket?: string | null
  raw_excerpt?: string | null
  evidence: Record<string, unknown>
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
}

/** One Check executed against one Target — the atomic result (`GET /runs?request_id=`). */
export interface CheckRunRow {
  run_id: string
  request_id?: string | null
  test_id?: string | null
  check_id: string
  target_id?: string | null
  requested_by: string
  request_source: string
  depth: Depth
  expectation: Expectation
  /** False for an `advisory=True` check: reported, never sets the verdict. */
  gating: boolean
  run_as_role?: string | null
  risk_tier: string
  verdict: Verdict
  failure_bucket?: string | null
  failure_stage?: string | null
  message: string
  workspace_name?: string | null
  git_sha: string
  app_version: string
  duration_ms?: number | null
  started_at?: string | null
  finished_at?: string | null
  created_at: string
  check_name: string
  /** `bucket @ stage` (ADR-0001) — null when the run passed. */
  bucket_at_stage?: string | null
}

/** A single CheckRun's detail (`GET /runs/{run_id}`) — stage outcomes + derived view. */
export interface CheckRunDetail extends CheckRunRow {
  stage_outcomes: StageOutcome[]
  connectable: boolean | null
  functional: boolean | null
}

// ---------------- Findings feed — "What's Interesting" (ADR-0009; #33/#34/#35) ---
/** A lane in the two-lane triage feed (mirrors app.services.rca LANE_*). */
export type FindingLane = 'recent-deployments' | 'overnight'

/** A run node in a provenance thread — the last-green or the first-bad. */
export interface ProvenanceRun {
  run_id: string
  check_id: string
  target_id?: string | null
  verdict: Verdict
  failure_bucket?: string | null
  failure_stage?: string | null
  /** `bucket @ stage` (ADR-0001) — null when the run passed. */
  bucket_at_stage?: string | null
  git_sha: string
  event_ref?: string | null
  created_at?: string | null
}

/** How the last-green → first-bad boundary is keyed: a commit sha (a deploy) or
 *  a non-deploy event ref ("metastore grant change · 21:14"). */
export interface BoundaryRef {
  kind: 'commit' | 'event'
  value: string
}

/** The last-green → first-bad troubleshooting spine for a `(check, target)`
 *  series (#34). Honest across the retention edge (the sweeper's preserved
 *  anchor keeps the last green even when it predates the window). */
export interface ProvenanceThread {
  check_id: string
  target_id?: string | null
  last_green?: ProvenanceRun | null
  first_bad?: ProvenanceRun | null
  boundary_ref?: BoundaryRef | null
  total_runs: number
}

/** A Test or Suite named on a finding so triage can deep-link to it. */
export interface FindingRef {
  id: string
  name: string
}

/** One finding — the current failing state of a `(target, check)` series. Not a
 *  raw run: a check failing five runs running is one finding (ADR-0009). */
export interface Finding {
  check_id: string
  check_name: string
  target_id?: string | null
  workspace_name?: string | null
  run_id: string
  request_id?: string | null
  tests?: FindingRef[]
  suites?: FindingRef[]
  verdict: Verdict
  failure_bucket?: string | null
  failure_stage?: string | null
  /** The `bucket @ stage` label (ADR-0001). */
  bucket_at_stage?: string | null
  /** The bucket's human label from the taxonomy ("Unexpected result"). */
  failure_label?: string | null
  lane: FindingLane
  /** False for an `advisory=True` check: reported, never fails a run. */
  gating: boolean
  request_source: string
  git_sha: string
  message: string
  provenance?: ProvenanceThread | null
  /** Advisory only — recent verdicts alternate. Never gates. */
  flaky: boolean
  finished_at?: string | null
  created_at: string
}

/** One lane of the feed: ranked findings + the collapsed non-actionable count
 *  (nominal/recovered series, ADR-0009 §6). */
export interface FindingsLane {
  lane: FindingLane
  findings: Finding[]
  everything_else: number
}

/** The two-lane findings feed (`GET /findings`). */
export interface FindingsFeed {
  generated_at: string
  window_hours: number
  lanes: FindingsLane[]
}

// ---------------- Audit (config-plane mutations, ADR-0014) ----------------
export interface AuditEvent {
  id: string
  ts: string
  actor: string
  actor_kind: string
  action: string
  entity_type: string
  entity_id?: string | null
  summary: string
}

// ---------------- Target (rebuilt model, ADR-0003/0004/0018; spec #24) --------
export type SafetyClass = 'read-only' | 'sandbox' | 'prod-for-us'

/** An assumable role-profile registered with a Target (ADR-0018). */
export interface AssumableRole {
  group_id: string
  label: string
  safety_class: SafetyClass
}

export interface Target {
  id: string
  name: string
  workspace_name?: string | null
  workspace_id?: string | null
  host?: string | null
  /** Authoritative cap on the max risk tier this target will run (ADR-0003). */
  safety_class: SafetyClass
  /** The target's OAuth M2M SP as a secret-scope `scope/key` handle. */
  identity_handle?: string | null
  assumable_roles: AssumableRole[]
  tags?: Record<string, unknown>
  enabled: boolean
}

// ---------------- Tests + the Assistant ----------------
export type TestStudioStatus = 'draft' | 'active'

export interface StudioTest {
  id: string
  name: string
  description: string
  tags: string[]
  status: TestStudioStatus
  run_as_role?: string | null
  version: number
  enabled: boolean
  updated_at?: string
  /** The Python test script. */
  script?: string | null
  /** The script's checks, in script order. */
  members: {
    check_id: string
    kind: string
    run_as_role?: string | null
    sort_order: number
  }[]
}

export interface RcaSuggestion {
  suggested_cause: string
  suggested_lane: string
  confidence: string
  evidence: Record<string, unknown>
  model_available: boolean
  advisory: boolean
}

/** One tool the Assistant called this turn, surfaced for transparency (#65): the
 *  tool name, the args it was invoked with, and its result. Rendered as a
 *  collapsed one-line indicator, expandable to show args/result. */
export interface ToolCall {
  name: string
  arguments: Record<string, unknown>
  result: unknown
}

/** A dynamic follow-up button offered after an answer (#65). Driven generically
 *  by the tool registry, not a hardcoded per-capability set: `author` seeds the
 *  shelf input with a starting sentence; `navigate` routes the main pane. */
export type NextActionKind = 'author' | 'navigate'
export interface NextAction {
  kind: NextActionKind
  label: string
  /** For `author`: the sentence to pre-fill the input with. */
  seed?: string
  /** For `navigate`: the route to send the main pane to. */
  href?: string
}

/** `authoring` means "write a test": the shelf hands the request to the test
 *  editor, with an optional short `answer` alongside. */
export interface AssistantTurnResponse {
  mode: 'authoring' | 'rca' | 'query'
  page: string
  rca?: RcaSuggestion
  answer?: string | null
  /** Which tools the agent called this turn, with args/result (additive, #65). */
  tool_calls?: ToolCall[]
  /** Contextual follow-up buttons derived from the tools that ran (additive, #65). */
  next_actions?: NextAction[]
}

export interface PageContext {
  page: string
  target_id?: string | null
  target_ids?: string[]
  test_id?: string | null
  suite_id?: string | null
  finding?: { check_id: string; target_id?: string | null } | null
}

export interface TestVersion {
  version: number
  actor: string
  created_at: string
  snapshot: Record<string, unknown>
}

// ---------------- Script Tests + Test editor ----------------
export interface ScriptDiagnostic {
  line: number
  message: string
}

export interface ScriptCheckInfo {
  name: string
  kind: 'assertion' | 'counterfactual'
  line: number
  role?: string | null
  expected_stage?: string | null
  advisory: boolean
  description?: string
}

export interface ScriptCallInfo {
  op: string
  verb: string
  tier: string
  line: number
  blocklisted: boolean
}

/** Static analysis of a script (`POST /scripts/analyze`) — never executes it. */
export interface ScriptAnalysis {
  ok: boolean
  errors: ScriptDiagnostic[]
  warnings: ScriptDiagnostic[]
  checks: ScriptCheckInfo[]
  calls: ScriptCallInfo[]
  max_risk_tier: string
}

export interface TestScript {
  script: string
  analysis: ScriptAnalysis
}

/** One brokered call the script made during a run. */
export interface ScriptCallRecord {
  op: string
  verb?: string
  tier?: string | null
  role?: string
  outcome: 'ok' | 'error' | 'blocked' | 'skipped' | string
  message?: string
  error_kind?: string
  ms?: number
  line?: number | null
  status_code?: number
  overridden?: boolean
  demo?: boolean
  [key: string]: unknown
}

export type ScriptCheckOutcome = 'pass' | 'fail' | 'error' | 'blocked' | 'skipped' | 'not_reached'

export interface ScriptCheckResult {
  name: string
  kind: string
  role?: string | null
  advisory: boolean
  description: string
  expected_stage?: string | null
  outcome: ScriptCheckOutcome
  stage?: string | null
  bucket?: string | null
  message: string
  traceback?: string | null
  line?: number | null
  evidence: Record<string, unknown>
  calls: ScriptCallRecord[]
  ms?: number | null
}

export interface ScriptError {
  kind: 'syntax' | 'error' | 'timeout' | 'crash' | string
  message: string
  line?: number | null
  traceback?: string | null
}

export interface ScriptRunResult {
  checks: ScriptCheckResult[]
  error: ScriptError | null
  output: string
  logs: string[]
  setup_calls: ScriptCallRecord[]
  duration_ms: number
  timed_out: boolean
}

export interface DryRunResponse {
  analysis: ScriptAnalysis
  result: ScriptRunResult | null
  target: { id: string; name: string; safety_class: string } | null
  depth: Depth
  /** The script the run executed (set on the editor agent's runs). */
  script?: string
}

export interface EditorChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface EditorStep {
  tool: string
  /** What the call did, in words ("Searching the SDK for …"). */
  detail: string
  ok: boolean
  /** A few words on what came back ("12 results", "failed: …"). */
  result?: string
  ms?: number
}

/** One event of `POST /assistant/editor-turn/stream`; the last is `done`. */
export type EditorEvent =
  | { type: 'thinking'; round: number }
  | { type: 'message'; text: string }
  | { type: 'reasoning'; text: string }
  | { type: 'notice'; text: string }
  | { type: 'proposal'; proposal: EditorProposal }
  | ({ type: 'tool_start'; step: number } & Pick<EditorStep, 'tool' | 'detail'>)
  | ({ type: 'tool_end'; step: number } & EditorStep)
  | ({ type: 'done' } & EditorTurnResponse)

/** The assistant's working log for one turn, as shown in the chat. */
export type EditorWorkItem =
  | { kind: 'message'; text: string }
  | { kind: 'reasoning'; text: string }
  | { kind: 'notice'; text: string }
  | ({ kind: 'tool'; running: boolean } & EditorStep)

export type ReasoningLevel = 'off' | 'low' | 'medium' | 'high'

export interface AssistantConfig {
  endpoint: string | null
  default_endpoint: string | null
  model: string | null
  reasoning: ReasoningLevel
  max_tool_calls: number
  max_output_tokens: number
  temperature: number
  turn_timeout_seconds: number
  allow_live_reads: boolean
  allow_dry_run: boolean
  updated_by: string
  updated_at: string
  limits: Record<string, [number, number]>
}

export type AssistantConfigUpdate = Partial<Omit<AssistantConfig, 'default_endpoint' | 'model' | 'updated_by' | 'updated_at' | 'limits'>>

export interface AssistantModel {
  name: string
  /** The served model, e.g. `system.ai.databricks-claude-sonnet-4-5`. */
  entity: string | null
  ready: boolean
  /** A pay-per-token foundation model (`system.ai.*`). */
  foundation: boolean
}

export interface ModelTestResult {
  ok: boolean
  ms: number
  error?: string
  called_tools?: boolean
  reasoning_shown?: boolean
  notes?: string[]
}

export interface EditorProposal {
  script: string
  summary: string
  analysis: ScriptAnalysis
}

export interface EditorTurnResponse {
  reply: string
  proposal?: EditorProposal | null
  steps: EditorStep[]
  dry_run?: DryRunResponse | null
  /** True when the turn failed (e.g. no model endpoint); `reply` says why. */
  error?: boolean
  /** True when the human stopped the turn. */
  stopped?: boolean
}

/** `GET /scripts/sdk/index`: {service: {method: {verb, summary, signature}}}. */
export type SdkIndex = Record<string, Record<string, { verb: string; summary: string; signature: string }>>

export interface SdkMethodDetail {
  service: string
  method: string
  verb: string
  rest_verb: string
  path: string
  summary: string
  tier: string
  call: string
  doc?: string
  blocklisted?: boolean
  blocklist_reason?: string
}

// ---------------- Branding (runtime white-label, ADR-0013 / #38) ----------------
/** One themeable design token, resolved in both schemes (hex colours). */
export interface BrandToken {
  light: string
  dark: string
}

/** The closed named-token theme: a headline accent + the closed token palette. */
export interface BrandTheme {
  accent: string
  tokens: Record<string, BrandToken>
}

/** The global branding record the shell reads on load + via poll. Blobs are
 *  served from `/branding/logo` + `/branding/favicon`; only flags travel here. */
export interface Branding {
  app_name: string
  assistant_name: string
  footer_text: string
  theme: BrandTheme
  has_logo: boolean
  logo_content_type?: string | null
  has_favicon: boolean
  favicon_content_type?: string | null
  updated_at: string
  updated_by: string
}

/** One shipped release from `RELEASE_NOTES.md`, served by `/admin/release-notes`. */
export interface ReleaseNote {
  version: string
  date: string
  summary: string
  sections: { title: string; items: string[] }[]
}

export interface ReleaseNotes {
  /** The version the backend is running — marks which release is deployed. */
  current_version: string
  /** Newest release first. */
  releases: ReleaseNote[]
}
