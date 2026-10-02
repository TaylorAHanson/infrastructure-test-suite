import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import type {
  AssistantTurnResponse,
  Depth,
  DryRunResponse,
  EditorChatMessage,
  EditorTurnResponse,
  ScriptAnalysis,
  SdkIndex,
  SdkMethodDetail,
  TestScript,
  AssumableRole,
  AuditEvent,
  Branding,
  Check,
  CheckRunDetail,
  CheckRunRow,
  FindingsFeed,
  PageContext,
  ReleaseNotes,
  RunRequestOut,
  RunRequestSummary,
  SafetyClass,
  StudioTest,
  Suite,
  Target,
  TestVersion,
  VerdictResource,
  WorkedExample,
} from '../types'

// ---------------- Catalog: Checks / Tests / Suites ----------------
/** The Check catalog — the atomic operation+expectation+evaluator units. */
export const useChecks = () =>
  useQuery({ queryKey: ['checks'], queryFn: () => api.get<Check[]>('/checks') })

/** The Test catalog (`/tests`) — target-agnostic ordered Check bundles. */
export const useTests = () =>
  useQuery({ queryKey: ['tests'], queryFn: () => api.get<StudioTest[]>('/tests') })

export const useSuites = () =>
  useQuery({ queryKey: ['suites'], queryFn: () => api.get<Suite[]>('/suites') })

export const useTargets = () =>
  useQuery({ queryKey: ['targets'], queryFn: () => api.get<Target[]>('/targets') })

// ---------------- The Assistant + Test Studio (spec #22) ----------------
export const useStudioTests = () =>
  useQuery({ queryKey: ['studio-tests'], queryFn: () => api.get<StudioTest[]>('/tests') })

export const useTestVersions = (testId?: string) =>
  useQuery({
    queryKey: ['test-versions', testId],
    queryFn: () => api.get<TestVersion[]>(`/tests/${testId}/versions`),
    enabled: !!testId,
  })

// Keyed on the Test's version so an accept or rollback refetches the export.
export const useTestYaml = (testId: string, version: number) =>
  useQuery({
    queryKey: ['test-yaml', testId, version],
    queryFn: () => api.get<{ yaml: string }>(`/tests/${testId}/yaml`),
  })

export const useAssistantTurn = () =>
  useMutation({
    mutationFn: (body: { turn: string; page_context: PageContext; mode?: string }) =>
      api.post<AssistantTurnResponse>('/assistant/turn', body),
  })

export interface DraftCheckBody {
  operation: { service: string; method: string; verb?: string; params?: Record<string, unknown> }
  handler_key: string
  assertion: string
  kind?: string
  counterpart_index?: number | null
  run_as_role?: string | null
}

export interface CommitDraftBody {
  name: string
  description?: string
  tags?: string[]
  checks: DraftCheckBody[]
  run_as_role?: string | null
  suite_id?: string | null
  derivation?: Record<string, unknown>
}

export const useCommitDraft = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CommitDraftBody) => api.post<StudioTest>('/assistant/draft', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['studio-tests'] }),
  })
}

export interface SuiteBody {
  name: string
  description?: string
  tags?: string[]
  test_ids: string[]
  deterministic_only?: boolean
  enabled?: boolean
}

export const useCreateSuite = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SuiteBody) => api.post<Suite>('/suites', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suites'] }),
  })
}

export const useUpdateSuite = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<SuiteBody> & { id: string }) =>
      api.patch<Suite>(`/suites/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suites'] }),
  })
}

export const useDeleteSuite = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.del<void>(`/suites/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suites'] }),
  })
}

// ---------------- Script Tests + the Test editor ----------------
const invalidateTest = (qc: ReturnType<typeof useQueryClient>, testId?: string) => {
  qc.invalidateQueries({ queryKey: ['studio-tests'] })
  qc.invalidateQueries({ queryKey: ['tests'] })
  qc.invalidateQueries({ queryKey: ['checks'] })
  if (testId) {
    qc.invalidateQueries({ queryKey: ['test-script', testId] })
    qc.invalidateQueries({ queryKey: ['test-versions', testId] })
  }
}

export const useTestScript = (testId?: string) =>
  useQuery({
    queryKey: ['test-script', testId],
    queryFn: () => api.get<TestScript>(`/tests/${testId}/script`),
    enabled: !!testId,
    staleTime: Infinity,
  })

export const useScriptTemplate = (enabled: boolean) =>
  useQuery({
    queryKey: ['script-template'],
    queryFn: () => api.get<{ script: string }>('/scripts/template'),
    enabled,
    staleTime: Infinity,
  })

export interface ScriptTestBody {
  name: string
  description?: string
  tags?: string[]
  script?: string
  run_as_role?: string | null
}

export const useCreateScriptTest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ScriptTestBody) => api.post<StudioTest>('/tests/script', body),
    onSuccess: (t) => invalidateTest(qc, t.id),
  })
}

export const useSaveScript = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ testId, ...body }: Partial<ScriptTestBody> & { testId: string; script: string }) =>
      api.put<StudioTest>(`/tests/${testId}/script`, body),
    onSuccess: (_t, v) => invalidateTest(qc, v.testId),
  })
}

export const useDeleteTest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (testId: string) => api.del<void>(`/tests/${testId}`),
    onSuccess: () => invalidateTest(qc),
  })
}

export const useAnalyzeScript = () =>
  useMutation({
    mutationFn: (script: string) => api.post<ScriptAnalysis>('/scripts/analyze', { script }),
  })

export interface DryRunBody {
  script: string
  target_id: string
  depth: Depth
  run_as_role?: string | null
}

export const useDryRun = () =>
  useMutation({
    mutationFn: (body: DryRunBody) => api.post<DryRunResponse>('/scripts/dry-run', body),
  })

export const useSdkIndex = () =>
  useQuery({
    queryKey: ['sdk-index'],
    queryFn: () => api.get<SdkIndex>('/scripts/sdk/index'),
    staleTime: Infinity,
  })

export const fetchSdkMethod = (service: string, method: string) =>
  api.get<SdkMethodDetail>(`/scripts/sdk/${encodeURIComponent(service)}?method=${encodeURIComponent(method)}`)

export interface EditorTurnBody {
  messages: EditorChatMessage[]
  script: string
  test_id?: string | null
  target_id?: string | null
  last_dry_run?: DryRunResponse | null
}

export const useEditorTurn = () =>
  useMutation({
    mutationFn: (body: EditorTurnBody) => api.post<EditorTurnResponse>('/assistant/editor-turn', body),
  })

export const useAcceptTest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (testId: string) => api.post<StudioTest>(`/tests/${testId}/accept`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['studio-tests'] }),
  })
}

export const useRollbackTest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ testId, version }: { testId: string; version: number }) =>
      api.post<StudioTest>(`/tests/${testId}/rollback/${version}`),
    onSuccess: (_d, v) => invalidateTest(qc, v.testId),
  })
}

export const usePromoteExample = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (testId: string) =>
      api.post<{ id: string }>('/assistant/examples/promote', { test_id: testId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['examples'] }),
  })
}

export const useExamples = () =>
  useQuery({ queryKey: ['examples'], queryFn: () => api.get<WorkedExample[]>('/assistant/examples') })

/** The curated onboarding starter prompts (spec #56 stories 30/32): the 3-4
 *  clickable examples the Assistant shelf shows a first-time user with an empty
 *  conversation, sourced from the WorkedExample library via `search_examples`. */
export const useStarterPrompts = () =>
  useQuery({
    queryKey: ['examples', 'onboarding'],
    queryFn: () => api.get<WorkedExample[]>('/assistant/examples?q=onboarding'),
  })

// ---------------- Targets (rebuilt model + friction ceremony, ADR-0010; #37) ----
export interface CreateTargetBody {
  name: string
  workspace_name?: string | null
  host?: string | null
  safety_class: SafetyClass
  identity_handle?: string | null
  assumable_roles?: AssumableRole[]
}

/** The friction-ceremony inputs required to *downgrade* a safety class (ADR-0010). */
export interface SafetyCeremony {
  typed_confirmation: string
  reason: string
}

export interface UpdateTargetBody {
  name?: string
  workspace_name?: string | null
  host?: string | null
  safety_class?: SafetyClass
  identity_handle?: string | null
  assumable_roles?: AssumableRole[]
  enabled?: boolean
  ceremony?: SafetyCeremony
}

export const useCreateTarget = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateTargetBody) => api.post<Target>('/targets', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['targets'] }),
  })
}

export const useUpdateTarget = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateTargetBody }) =>
      api.patch<Target>(`/targets/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['targets'] }),
  })
}

export const useDeleteTarget = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.del(`/targets/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['targets'] }),
  })
}

// ---------------- Runs (rebuilt Run API, ADR-0005/0011) ----------------
/** The Runs list = the submitted RunRequests (`GET /runs/requests`). Each row's
 *  phase + verdict are derived from its `status` (see lib/format `phaseOf`). */
export const useRuns = () =>
  useQuery({
    queryKey: ['runs'],
    queryFn: () => api.get<RunRequestSummary[]>('/runs/requests'),
    refetchInterval: 5000,
  })

/** A Run's aggregate verdict resource (`GET /runs/{req_id}`). Polls until terminal. */
export const useRunResource = (runId?: string) =>
  useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.get<VerdictResource>(`/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (q) => ((q.state.data as VerdictResource)?.phase === 'terminal' ? false : 3000),
  })

/** The fan-out CheckRuns for a request (`GET /runs?request_id=…`). */
export const useRunCheckRuns = (requestId?: string) =>
  useQuery({
    queryKey: ['run-checkruns', requestId],
    queryFn: () => api.get<CheckRunRow[]>(`/runs?request_id=${requestId}`),
    enabled: !!requestId,
    refetchInterval: 4000,
  })

/** A single CheckRun's stage outcomes + derived view (`GET /runs/{run_id}`). */
export const useCheckRun = (runId?: string, enabled = true) =>
  useQuery({
    queryKey: ['checkrun', runId],
    queryFn: () => api.get<CheckRunDetail>(`/runs/${runId}`),
    enabled: !!runId && enabled,
  })

/** A per-run safety-gate override (ADR-0004 §5/#81) — human-only, GUI-only,
 *  exactly one elevated tier (R2) on a `prod-for-us` target. `target_name` is
 *  the value the operator typed to confirm; the gate rejects it for any
 *  non-UI_MANUAL request source and for any class/tier other than that one. */
export interface SafetyOverrideBody {
  target_name: string
  reason: string
  confirmed: boolean
}

export interface SubmitRunBody {
  suite_ids?: string[]
  test_ids?: string[]
  check_ids?: string[]
  target_ids?: string[]
  depth?: 'quick' | 'full'
  requested_by?: string
  request_source?: string
  override?: SafetyOverrideBody
}

export const useSubmitRun = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SubmitRunBody) => api.post<RunRequestOut>('/runs', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['runs'] }),
  })
}

// ---------------- Audit (read-only, config-plane only; ADR-0014) ----------------
export interface AuditFilters {
  target?: string
  actor?: string
  event_type?: string
}

const auditQuery = (filters: AuditFilters): string => {
  const p = new URLSearchParams()
  if (filters.target) p.set('target', filters.target)
  if (filters.actor) p.set('actor', filters.actor)
  if (filters.event_type) p.set('event_type', filters.event_type)
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const useAudit = (filters: AuditFilters = {}) =>
  useQuery({
    queryKey: ['audit', filters],
    queryFn: () => api.get<AuditEvent[]>(`/audit${auditQuery(filters)}`),
  })

export const useAuditEventTypes = () =>
  useQuery({ queryKey: ['audit-event-types'], queryFn: () => api.get<string[]>('/audit/event-types') })

/** A same-filters download URL for the CSV/JSON export endpoint. */
export const auditExportUrl = (filters: AuditFilters, format: 'csv' | 'json'): string => {
  const q = auditQuery(filters)
  return `/api/v1/audit/export${q ? `${q}&` : '?'}format=${format}`
}

export const useAdminSettings = () =>
  useQuery({ queryKey: ['admin'], queryFn: () => api.get<Record<string, unknown>>('/admin/settings') })

/** Release notes ship with the code, so they only change on a deploy — fetch once. */
export const useReleaseNotes = () =>
  useQuery({
    queryKey: ['release-notes'],
    queryFn: () => api.get<ReleaseNotes>('/admin/release-notes'),
    staleTime: Infinity,
  })

// ---------------- Branding (runtime white-label; ADR-0013 / #38) ----------------
/** The global branding record. The shell reads it on load and this **poll**
 *  refreshes it, so a branding edit applies within the poll interval — the
 *  instant-apply contract (ADR-0013 §4), no redeploy or push infra. */
export const useBranding = () =>
  useQuery({
    queryKey: ['branding'],
    queryFn: () => api.get<Branding>('/branding'),
    refetchInterval: 30_000,
  })

export interface BrandingUpdateBody {
  app_name?: string
  assistant_name?: string
  footer_text?: string
  theme?: Branding['theme']
  logo?: string | null
  favicon?: string | null
  clear_logo?: boolean
  clear_favicon?: boolean
}

export const useUpdateBranding = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BrandingUpdateBody) => api.patch<Branding>('/branding', body),
    onSuccess: (data) => {
      qc.setQueryData(['branding'], data)
      qc.invalidateQueries({ queryKey: ['branding'] })
      qc.invalidateQueries({ queryKey: ['audit'] })
    },
  })
}

// ---------------- Run-completion webhook sink (ADR-0016) ----------------
export interface WebhookConfig {
  id: string
  enabled: boolean
  urls: string[]
  fire_on_all_completions: boolean
  updated_by: string
  updated_at: string
}
export interface WebhookConfigUpdate {
  urls?: string[]
  enabled?: boolean
  fire_on_all_completions?: boolean
}

export const useWebhookConfig = () =>
  useQuery({ queryKey: ['webhook-config'], queryFn: () => api.get<WebhookConfig>('/webhook/config') })

export const useUpdateWebhookConfig = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: WebhookConfigUpdate) => api.put<WebhookConfig>('/webhook/config', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['webhook-config'] }),
  })
}

// ---------------- Findings feed — "What's Interesting" (ADR-0009; #33/#34/#35) ----
/** The two-lane triage feed (`GET /findings`): findings, not raw runs, split
 *  into Recent-deployments + Overnight with the non-actionable count collapsed.
 *  Polls so a triager watching the screen sees new regressions land. */
export const useFindings = ({ windowHours, limit }: { windowHours?: number; limit?: number } = {}) => {
  const qs = new URLSearchParams()
  if (windowHours != null) qs.set('window_hours', String(windowHours))
  if (limit != null) qs.set('limit', String(limit))
  const suffix = qs.toString() ? `?${qs}` : ''
  return useQuery({
    queryKey: ['findings', windowHours ?? null, limit ?? null],
    queryFn: () => api.get<FindingsFeed>(`/findings${suffix}`),
    refetchInterval: 15000,
  })
}
