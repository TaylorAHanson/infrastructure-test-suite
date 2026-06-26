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

export const useSuites = () =>
  useQuery({ queryKey: ['suites'], queryFn: () => api.get<Suite[]>('/suites') })

export const useTargets = () =>
  useQuery({ queryKey: ['targets'], queryFn: () => api.get<Target[]>('/targets') })

export const useCatalogs = () =>
  useQuery({ queryKey: ['catalogs'], queryFn: () => api.get<string[]>('/targets/catalogs') })

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
