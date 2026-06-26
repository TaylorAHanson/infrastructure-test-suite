import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import type {
  AuditEvent,
  RemoteAgent,
  Run,
  Schedule,
  Suite,
  Target,
  TestDefinition,
} from '../types'

// ---------------- Catalog ----------------
export const useTests = () =>
  useQuery({ queryKey: ['tests'], queryFn: () => api.get<TestDefinition[]>('/tests') })

export const useCreateTest = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post<TestDefinition>('/tests', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tests'] }),
  })
}

// ---------------- AI authoring ----------------
export interface AuthoringCapabilities {
  model_serving_endpoint: string | null
  ai_available: boolean
  executors: { executor_key: string; kind: string; purpose: string; params: Record<string, string> }[]
}

export const useAuthoringCapabilities = () =>
  useQuery({
    queryKey: ['authoring-capabilities'],
    queryFn: () => api.get<AuthoringCapabilities>('/authoring/capabilities'),
  })

export const useGenerateTest = () =>
  useMutation({
    mutationFn: (body: { description: string; category?: string }) =>
      api.post<{ spec: Record<string, unknown>; endpoint: string | null }>('/authoring/generate', body),
  })

export const useSuites = () =>
  useQuery({ queryKey: ['suites'], queryFn: () => api.get<Suite[]>('/suites') })

export const useTargets = () =>
  useQuery({ queryKey: ['targets'], queryFn: () => api.get<Target[]>('/targets') })

export const useCatalogs = () =>
  useQuery({ queryKey: ['catalogs'], queryFn: () => api.get<string[]>('/targets/catalogs') })

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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['targets'] })
      qc.invalidateQueries({ queryKey: ['catalogs'] })
    },
  })
}

export const useDeleteTarget = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.del(`/targets/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['targets'] })
      qc.invalidateQueries({ queryKey: ['catalogs'] })
    },
  })
}

// ---------------- Runs ----------------
export interface RunFilters {
  status?: string
  category?: string
  catalog?: string
  suite_id?: string
  target_id?: string
  test_definition_id?: string
  requester?: string
}

export const useRuns = (filters: RunFilters = {}) => {
  const qs = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v) as [string, string][],
  ).toString()
  return useQuery({
    queryKey: ['runs', filters],
    queryFn: () => api.get<Run[]>(`/runs${qs ? `?${qs}` : ''}`),
    refetchInterval: 5000,
  })
}

export const useRun = (runId?: string) =>
  useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.get<Run>(`/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (q) =>
      ['RUNNING', 'QUEUED', 'PENDING'].includes((q.state.data as Run)?.status) ? 3000 : false,
  })

export interface SubmitRunBody {
  suite_ids?: string[]
  test_definition_ids?: string[]
  target_ids?: string[]
  catalogs?: string[]
  execution_mode: string
  requested_by?: string
}

export const useSubmitRun = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SubmitRunBody) => api.post<{ id: string }>('/runs', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['runs'] }),
  })
}

// ---------------- Schedules ----------------
export const useSchedules = () =>
  useQuery({ queryKey: ['schedules'], queryFn: () => api.get<Schedule[]>('/schedules') })

export const useToggleSchedule = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.patch<Schedule>(`/schedules/${id}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  })
}

export const useFireSchedule = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post(`/schedules/${id}/fire`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['runs'] }),
  })
}

// ---------------- Misc ----------------
export const useAudit = () =>
  useQuery({ queryKey: ['audit'], queryFn: () => api.get<AuditEvent[]>('/audit') })

export const useAgents = () =>
  useQuery({ queryKey: ['agents'], queryFn: () => api.get<RemoteAgent[]>('/agents') })

export const useAdminSettings = () =>
  useQuery({ queryKey: ['admin'], queryFn: () => api.get<Record<string, unknown>>('/admin/settings') })

// ---------------- Analytics ----------------
export const useAnalytics = <T,>(name: string, params = '') =>
  useQuery({ queryKey: ['analytics', name, params], queryFn: () => api.get<T>(`/analytics/${name}${params}`) })
