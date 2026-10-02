import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { Badge } from '../components/Badge'
import { PageHeader } from '../components/PageHeader'
import { RunLauncher } from '../components/RunLauncher'
import { useRuns } from '../hooks/queries'
import { phaseOf, timeAgo } from '../lib/format'
import type { Phase } from '../types'

const PHASES: (Phase | '')[] = ['', 'queued', 'running', 'terminal']
const VERDICTS = ['', 'PASS', 'FAIL', 'ERROR', 'BLOCKED_BY_SAFETY']
const DEPTHS = ['', 'quick', 'full']

interface Filters {
  phase?: string
  verdict?: string
  depth?: string
  requester?: string
}

function targetsLabel(ids: string[]): string {
  return ids.length ? `${ids.length} selected` : 'all'
}

export default function Runs() {
  const { data: runs, isLoading } = useRuns()
  const [filters, setFilters] = useState<Filters>({})
  const navigate = useNavigate()

  // Clicks on the inner link, or that end a text selection, are left alone so
  // the link's own cmd/middle-click behavior and copy-by-drag keep working.
  const openRun = (id: string) => (e: React.MouseEvent<HTMLTableRowElement>) => {
    if ((e.target as HTMLElement).closest('a')) return
    if (window.getSelection()?.toString()) return
    if (e.metaKey || e.ctrlKey) window.open(`/runs/${id}`, '_blank')
    else navigate(`/runs/${id}`)
  }

  const set = (key: keyof Filters) => (e: React.ChangeEvent<HTMLSelectElement | HTMLInputElement>) =>
    setFilters((f) => ({ ...f, [key]: e.target.value || undefined }))

  const rows = useMemo(() => {
    return (runs ?? []).filter((r) => {
      if (filters.phase && phaseOf(r.status) !== filters.phase) return false
      if (filters.verdict && r.status !== filters.verdict) return false
      if (filters.depth && r.depth !== filters.depth) return false
      if (filters.requester && !r.requested_by.toLowerCase().includes(filters.requester.toLowerCase()))
        return false
      return true
    })
  }, [runs, filters])

  return (
    <div>
      <PageHeader title="Runs" subtitle="Every run submitted through the shared Run API (CI, GUI, nightly)" />

      <div className="mb-4">
        <RunLauncher label="Manual Run" />
      </div>

      <div className="card mb-4 flex flex-wrap items-end gap-3">
        <span className="row-label">Filter by</span>
        <Filter label="Phase">
          <select className="input" onChange={set('phase')}>
            {PHASES.map((s) => <option key={s} value={s}>{s || 'Any'}</option>)}
          </select>
        </Filter>
        <Filter label="Verdict">
          <select className="input" onChange={set('verdict')}>
            {VERDICTS.map((s) => <option key={s} value={s}>{s || 'Any'}</option>)}
          </select>
        </Filter>
        <Filter label="Depth">
          <select className="input" onChange={set('depth')}>
            {DEPTHS.map((s) => <option key={s} value={s}>{s || 'Any'}</option>)}
          </select>
        </Filter>
        <Filter label="Requester">
          <input className="input" placeholder="any" onChange={set('requester')} />
        </Filter>
      </div>

      <div className="card overflow-hidden p-0">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink">
            <tr>
              <th className="th">Run</th>
              <th className="th">Phase</th>
              <th className="th">Verdict</th>
              <th className="th">Depth</th>
              <th className="th">Source</th>
              <th className="th">Targets</th>
              <th className="th">Requested by</th>
              <th className="th">When</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const phase = phaseOf(r.status)
              return (
                <tr
                  key={r.id}
                  className="cursor-pointer border-b border-edge/50 hover:bg-ink-soft"
                  onClick={openRun(r.id)}
                >
                  <td className="td">
                    <Link to={`/runs/${r.id}`} className="font-mono text-xs text-accent hover:underline">
                      {r.id}
                    </Link>
                    <div className="text-xs text-muted">{r.git_sha}</div>
                  </td>
                  <td className="td"><Badge value={phase} /></td>
                  <td className="td">{phase === 'terminal' ? <Badge value={r.status} /> : <span className="text-muted">—</span>}</td>
                  <td className="td text-muted">{r.depth}</td>
                  <td className="td text-muted">{r.request_source}</td>
                  <td className="td text-muted">{targetsLabel(r.target_ids)}</td>
                  <td className="td text-muted">{r.requested_by}</td>
                  <td className="td text-muted">{timeAgo(r.created_at)}</td>
                </tr>
              )
            })}
            {!isLoading && !rows.length && (
              <tr><td className="td text-muted" colSpan={8}>No runs match these filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Filter({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="card-title">{label}</span>
      {children}
    </div>
  )
}
