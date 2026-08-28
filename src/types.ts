// Shared TypeScript types mirroring the backend Pydantic/SQLModel schemas.

export type TestCategory = 'PRODUCER' | 'PROCESSING' | 'CONSUMER'
export type ExecutionMode = 'PREFLIGHT' | 'SMOKE' | 'FULL'
export type ProbeOutcome = 'PASS' | 'FAIL' | 'ERROR' | 'SKIP' | 'PARTIAL'
export type ConnectivityOutcome = 'CONNECTABLE' | 'NOT_CONNECTABLE' | 'UNKNOWN'
export type RunStatus =
  | 'PENDING' | 'QUEUED' | 'RUNNING' | 'PASSED' | 'FAILED' | 'PARTIAL' | 'ERROR' | 'CANCELLED'
export type StepKind = 'PRECHECK' | 'DNS' | 'AUTH' | 'NETWORK' | 'FUNCTIONAL' | 'CLEANUP'

export interface TestDefinition {
  id: string
  display_name: string
  description: string
  category: TestCategory
  tags: string[]
  target_type: string
  required_capabilities: string[]
  supported_execution_modes: ExecutionMode[]
  executor_kind: string
  executor_key: string
  auth_mode: string
  enterprise_only: boolean
  destructive: boolean
  enabled: boolean
  timeout_seconds: number
  cleanup_policy: string
}

export interface Suite {
  id: string
  name: string
  description: string
  tags: string[]
  enabled: boolean
  test_definition_ids: string[]
}

export interface Target {
  id: string
  name: string
  target_type: string
  catalog?: string | null
  schema_name?: string | null
  workspace_name?: string | null
  cloud: string
  region?: string | null
  enabled: boolean
}

export interface Schedule {
  id: string
  name: string
  cron: string
  timezone: string
  enabled: boolean
  suite_ids: string[]
  test_definition_ids: string[]
  target_ids: string[]
  catalogs: string[]
  execution_mode: ExecutionMode
  databricks_job_id?: string | null
  last_fired_at?: string | null
}

export interface RunStep {
  id: string
  step_kind: StepKind
  sort_order: number
  outcome: ProbeOutcome
  connectivity: ConnectivityOutcome
  duration_ms?: number | null
  message: string
  error_class?: string | null
  root_cause_bucket?: string | null
  recommendation?: string | null
  raw_payload_excerpt?: string | null
  evidence: Record<string, unknown>
}

export interface Run {
  run_id: string
  test_definition_id: string
  test_name?: string
  category?: string | null
  executor_key?: string | null
  target_id?: string | null
  catalog?: string | null
  workspace_name?: string | null
  cloud: string
  region?: string | null
  execution_mode: ExecutionMode
  requested_by: string
  request_source: string
  status: RunStatus
  probe_outcome?: ProbeOutcome | null
  connectivity_outcome?: ConnectivityOutcome | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  summary_counts: Record<string, number>
  connectivity_summary: Record<string, number>
  failure_buckets: string[]
  root_cause_bucket?: string | null
  recommendation?: string | null
  databricks_job_run_url?: string | null
  telemetry_logs_url?: string | null
  telemetry_trace_id?: string | null
  git_sha: string
  app_version: string
  created_at: string
  steps?: RunStep[]
}

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

export interface RemoteAgent {
  id: string
  name: string
  vantage_point: string
  capabilities: string[]
  status: string
  last_seen_at?: string | null
  enabled: boolean
}

// ---------------- Assistant / Test Studio (spec #22) ----------------
export type TestStudioStatus = 'draft' | 'active'

export interface Operation {
  service: string
  method: string
  verb?: string
  params?: Record<string, unknown>
}

export interface StudioTest {
  id: string
  name: string
  description: string
  tags: string[]
  status: TestStudioStatus
  origin: string
  run_as_role?: string | null
  derivation?: Record<string, unknown>
  version: number
  enabled: boolean
  updated_at?: string
  members: {
    check_id: string
    kind: string
    run_as_role?: string | null
    counterpart_check_id?: string | null
    sort_order: number
  }[]
}

export interface CheckProposal {
  display_name: string
  operation: Operation
  handler_key: string
  assertion: string
  expectation: string
  evaluator: string
  compilable: boolean
  risk_tier: string
  kind: string
  needs_tier_confirmation: boolean
}

export interface DraftProposal {
  name: string
  description: string
  checks: CheckProposal[]
  refusals: { operation: Operation; reason: string; tier: string }[]
  max_risk_tier: string
  suggested_counterfactual?: {
    assertion: string
    operation: Operation
    handler_key: string
    why: string
  } | null
  suggested_suite?: { suite_id: string; name: string; why: string } | null
  suggested_run_as_role?: { role: Record<string, string>; why: string } | null
  clarifying_question?: string | null
  derivation: Record<string, unknown>
}

export interface RcaSuggestion {
  suggested_cause: string
  suggested_lane: string
  confidence: string
  evidence: Record<string, unknown>
  model_available: boolean
  advisory: boolean
}

export interface AssistantTurnResponse {
  mode: 'authoring' | 'rca'
  page: string
  draft?: DraftProposal
  rca?: RcaSuggestion
  clarifying_question?: string
}

export interface PageContext {
  page: string
  target_id?: string | null
  target_ids?: string[]
  test_id?: string | null
  finding?: { check_id: string; target_id?: string | null } | null
}

export interface WorkedExample {
  id: string
  intent: string
  title: string
  operation: Operation
  assertion: string
  expectation: string
  tags: string[]
  curated: boolean
  source_test_id?: string | null
}

export interface TestVersion {
  version: number
  actor: string
  created_at: string
  snapshot: Record<string, unknown>
}
