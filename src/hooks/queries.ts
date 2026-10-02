import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'

import { api, postStream } from '../lib/api'
import type {
  AssistantConfig,
  AssistantConfigUpdate,
  AssistantModel,
  AssistantTurnResponse,
  Depth,
  DryRunResponse,
  EditorChatMessage,
  EditorEvent,
  ModelTestResult,
  ReasoningLevel,
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
} from '../types'

// ---------------- Catalog: Checks / Tests / Suites ----------------
/** Every script check (read-only): names, the calls each makes, risk tiers. */
export const useChecks = () =>
  useQuery({ queryKey: ['checks'], queryFn: () => api.get<Check[]>('/checks') })

/** The Test catalog (`/tests`, retired Tests excluded) — target-agnostic
 *  scripts with their Draft/Active status. One cache for every page. */
export const useTests = () =>
  useQuery({ queryKey: ['tests'], queryFn: () => api.get<StudioTest[]>('/tests'), retry: 1 })

/** One Test by id — unlike the list, this also finds a retired Test. */
export const useTest = (testId: string | undefined, enabled = true) =>
  useQuery({
    queryKey: ['test', testId],
    queryFn: () => api.get<StudioTest>(`/tests/${testId}`),
    enabled: !!testId && enabled,
    retry: false,
  })

export const useSuites = () =>
  useQuery({ queryKey: ['suites'], queryFn: () => api.get<Suite[]>('/suites') })

export const useTargets = () =>
  useQuery({ queryKey: ['targets'], queryFn: () => api.get<Target[]>('/targets') })

// ---------------- Tests + the Assistant shelf ----------------
export const useTestVersions = (testId?: string) =>
  useQuery({
    queryKey: ['test-versions', testId],
    queryFn: () => api.get<TestVersion[]>(`/tests/${testId}/versions`),
    enabled: !!testId,
  })

export const useAssistantTurn = () =>
  useMutation({
    mutationFn: (body: { turn: string; page_context: PageContext; mode?: string }) =>
      api.post<AssistantTurnResponse>('/assistant/turn', body),
  })

export interface SuiteBody {
  name: string
  description?: string
  tags?: string[]
  test_ids: string[]
  enabled?: boolean
}

const suiteChanged = (qc: QueryClient) => {
  qc.invalidateQueries({ queryKey: ['suites'] })
  qc.invalidateQueries({ queryKey: ['audit'] })
}

export const useCreateSuite = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SuiteBody) => api.post<Suite>('/suites', body),
    onSuccess: () => suiteChanged(qc),
  })
}

export const useUpdateSuite = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<SuiteBody> & { id: string }) =>
      api.patch<Suite>(`/suites/${id}`, body),
    onSuccess: () => suiteChanged(qc),
  })
}

export const useDeleteSuite = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.del<void>(`/suites/${id}`),
    onSuccess: () => suiteChanged(qc),
  })
}

// ---------------- Script Tests + the Test editor ----------------
/** After any Test mutation: write the returned Test into the list (so a page
 *  that navigates on success sees it immediately), then refetch everything that
 *  shows Tests — the list, one Test, its checks, script and versions, the
 *  suites (a delete drops its memberships) and the audit log. */
const testChanged = (qc: QueryClient, testId?: string, updated?: StudioTest) => {
  if (updated) {
    qc.setQueryData<StudioTest[]>(['tests'], (prev) =>
      prev && [...prev.filter((t) => t.id !== updated.id), updated])
    qc.setQueryData(['test', updated.id], updated)
  }
  qc.invalidateQueries({ queryKey: ['tests'] })
  qc.invalidateQueries({ queryKey: ['checks'] })
  qc.invalidateQueries({ queryKey: ['suites'] })
  qc.invalidateQueries({ queryKey: ['audit'] })
  if (testId) {
    qc.invalidateQueries({ queryKey: ['test', testId] })
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
    retry: 1,
  })

export const useScriptTemplate = (enabled: boolean) =>
  useQuery({
    queryKey: ['script-template'],
    queryFn: () => api.get<{ script: string }>('/scripts/template'),
    enabled,
    staleTime: Infinity,
    retry: 1,
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
    onSuccess: (t) => testChanged(qc, t.id, t),
  })
}

export const useSaveScript = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ testId, ...body }: Partial<ScriptTestBody> & { testId: string; script: string }) =>
      api.put<StudioTest>(`/tests/${testId}/script`, body),
    onSuccess: (t, v) => testChanged(qc, v.testId, t),
  })
}

export const useDeleteTest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (testId: string) => api.del<void>(`/tests/${testId}`),
    onSuccess: (_d, testId) => {
      qc.setQueryData<StudioTest[]>(['tests'], (prev) => prev?.filter((t) => t.id !== testId))
      testChanged(qc)
    },
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

export const streamEditorTurn = (body: EditorTurnBody, onEvent: (e: EditorEvent) => void, signal?: AbortSignal) =>
  postStream<EditorEvent>('/assistant/editor-turn/stream', body, onEvent, signal)

export const useAssistantConfig = () =>
  useQuery({ queryKey: ['assistant-config'], queryFn: () => api.get<AssistantConfig>('/assistant/config') })

export const useUpdateAssistantConfig = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: AssistantConfigUpdate) => api.put<AssistantConfig>('/assistant/config', body),
    onSuccess: (cfg) => {
      qc.setQueryData(['assistant-config'], cfg)
      qc.invalidateQueries({ queryKey: ['audit'] })
    },
  })
}

export const useAssistantModels = () =>
  useQuery({
    queryKey: ['assistant-models'],
    queryFn: () => api.get<{ models: AssistantModel[]; error: string | null }>('/assistant/models'),
    staleTime: 60_000,
  })

export const useTestAssistantModel = () =>
  useMutation({
    mutationFn: (body: { endpoint: string; reasoning: ReasoningLevel }) =>
      api.post<ModelTestResult>('/assistant/models/test', body),
  })

export const useAcceptTest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (testId: string) => api.post<StudioTest>(`/tests/${testId}/accept`),
    onSuccess: (t, testId) => testChanged(qc, testId, t),
  })
}

export const useRollbackTest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ testId, version }: { testId: string; version: number }) =>
      api.post<StudioTest>(`/tests/${testId}/rollback/${version}`),
    onSuccess: (t, v) => testChanged(qc, v.testId, t),
  })
}

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

const targetChanged = (qc: QueryClient) => {
  qc.invalidateQueries({ queryKey: ['targets'] })
  qc.invalidateQueries({ queryKey: ['audit'] })
}

export const useCreateTarget = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateTargetBody) => api.post<Target>('/targets', body),
    onSuccess: () => targetChanged(qc),
  })
}

export const useUpdateTarget = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateTargetBody }) =>
      api.patch<Target>(`/targets/${id}`, body),
    onSuccess: () => targetChanged(qc),
  })
}

export const useDeleteTarget = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.del(`/targets/${id}`),
    onSuccess: () => targetChanged(qc),
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

/** A Run's aggregate verdict resource (`GET /runs/{req_id}`). Polls until terminal
 *  (or until it fails, e.g. no such run). */
export const useRunResource = (runId?: string) =>
  useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.get<VerdictResource>(`/runs/${runId}`),
    enabled: !!runId,
    retry: 1,
    refetchInterval: (q) => (q.state.error || q.state.data?.phase === 'terminal' ? false : 3000),
  })

export const CHECK_RUN_PAGE = 200

/** The fan-out CheckRuns for a request (`GET /runs?request_id=…`), a page at a
 *  time. Polls while the run is live; `terminal` is part of the key so the run
 *  finishing triggers one last fetch of the final rows, then polling stops. */
export const useRunCheckRuns = (requestId: string | undefined, terminal: boolean) =>
  useInfiniteQuery({
    queryKey: ['run-checkruns', requestId, terminal],
    queryFn: ({ pageParam }) =>
      api.get<CheckRunRow[]>(
        `/runs?request_id=${encodeURIComponent(requestId!)}&limit=${CHECK_RUN_PAGE}&offset=${pageParam}`,
      ),
    initialPageParam: 0,
    getNextPageParam: (last, pages, lastOffset) => {
      if (last.length < CHECK_RUN_PAGE) return undefined
      // A server that ignores `offset` returns the first page again: stop there.
      const earlier = new Set(pages.slice(0, -1).flat().map((r) => r.run_id))
      if (last.every((r) => earlier.has(r.run_id))) return undefined
      return lastOffset + CHECK_RUN_PAGE
    },
    enabled: !!requestId,
    refetchInterval: terminal ? false : 4000,
    placeholderData: keepPreviousData,
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
  target_ids?: string[]
  depth?: 'quick' | 'full'
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['webhook-config'] })
      qc.invalidateQueries({ queryKey: ['audit'] })
    },
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
