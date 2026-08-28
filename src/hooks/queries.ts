import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import type {
  AssistantTurnResponse,
  AuditEvent,
  Check,
  CheckRunDetail,
  CheckRunRow,
  PageContext,
  RunRequestOut,
  RunRequestSummary,
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
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ['studio-tests'] })
      qc.invalidateQueries({ queryKey: ['test-versions', v.testId] })
    },
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

// ---------------- Targets (registration ceremony rebuilt in spec #24) ----------------
export interface CreateTargetBody {
  name: string
  target_type: string
  catalog?: string | null
  schema_name?: string | null
  workspace_name?: string | null
  cloud?: string
  region?: string | null
  config?: Record<string, unknown>
}

export const useCreateTarget = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateTargetBody) => api.post<Target>('/targets', body),
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

export interface SubmitRunBody {
  suite_ids?: string[]
  test_ids?: string[]
  check_ids?: string[]
  target_ids?: string[]
  depth?: 'quick' | 'full'
  requested_by?: string
  request_source?: string
}

export const useSubmitRun = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SubmitRunBody) => api.post<RunRequestOut>('/runs', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['runs'] }),
  })
}

// ---------------- Misc ----------------
// The Audit view is owned by spec #24; this hook stays for that page.
export const useAudit = () =>
  useQuery({ queryKey: ['audit'], queryFn: () => api.get<AuditEvent[]>('/audit') })

export const useAdminSettings = () =>
  useQuery({ queryKey: ['admin'], queryFn: () => api.get<Record<string, unknown>>('/admin/settings') })

// ---------------- Analytics (Dashboard + Analytics owned by spec #23) ----------------
export const useAnalytics = <T,>(name: string, params = '') =>
  useQuery({ queryKey: ['analytics', name, params], queryFn: () => api.get<T>(`/analytics/${name}${params}`) })
