import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge } from '../components/Badge'
import { PageHeader } from '../components/PageHeader'
import { RunLauncher } from '../components/RunLauncher'
import { useCatalogs, useRuns, useSuites, type RunFilters } from '../hooks/queries'
import { duration, timeAgo } from '../lib/format'

const STATUSES = ['', 'PASSED', 'FAILED', 'PARTIAL', 'ERROR', 'RUNNING', 'PENDING']
const CATEGORIES = ['', 'PRODUCER', 'PROCESSING', 'CONSUMER']

export default function Runs() {
  const [filters, setFilters] = useState<RunFilters>({})
  const { data: runs, isLoading } = useRuns(filters)
  const { data: suites } = useSuites()
  const { data: catalogs } = useCatalogs()

  const set = (key: keyof RunFilters) => (e: React.ChangeEvent<HTMLSelectElement | HTMLInputElement>) =>
    setFilters((f) => ({ ...f, [key]: e.target.value || undefined }))

  return (
    <div>
      <PageHeader title="Runs" subtitle="History & audit of every validation run" />

      <div className="mb-4">
        <RunLauncher />
      </div>

      <div className="card mb-4 flex flex-wrap items-end gap-3">
        <Filter label="Status">
          <select className="input" onChange={set('status')}>
            {STATUSES.map((s) => <option key={s} value={s}>{s || 'Any'}</option>)}
          </select>
        </Filter>
        <Filter label="Category">
          <select className="input" onChange={set('category')}>
            {CATEGORIES.map((s) => <option key={s} value={s}>{s || 'Any'}</option>)}
          </select>
        </Filter>
        <Filter label="Suite">
          <select className="input" onChange={set('suite_id')}>
            <option value="">Any</option>
            {suites?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Filter>
        <Filter label="Catalog">
          <select className="input" onChange={set('catalog')}>
            <option value="">Any</option>
            {catalogs?.map((c) => <option key={c} value={c}>{c}</option>)}
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
              <th className="th">Test</th>
              <th className="th">Category</th>
              <th className="th">Status</th>
              <th className="th">Connectivity</th>
              <th className="th">Catalog</th>
              <th className="th">Mode</th>
              <th className="th">Root cause</th>
              <th className="th">Duration</th>
              <th className="th">When</th>
            </tr>
          </thead>
          <tbody>
            {runs?.map((r) => (
              <tr key={r.run_id} className="border-b border-edge/50 hover:bg-ink-soft">
                <td className="td">
                  <Link to={`/runs/${r.run_id}`} className="text-accent hover:underline">
                    {r.test_name ?? r.test_definition_id}
                  </Link>
                </td>
                <td className="td text-muted">{r.category}</td>
                <td className="td"><Badge value={r.status} /></td>
                <td className="td"><Badge value={r.connectivity_outcome} /></td>
                <td className="td text-muted">{r.catalog ?? '-'}</td>
                <td className="td text-muted">{r.execution_mode}</td>
                <td className="td text-muted">{r.root_cause_bucket ?? '-'}</td>
                <td className="td text-muted">{duration(r.duration_ms)}</td>
                <td className="td text-muted">{timeAgo(r.finished_at ?? r.created_at)}</td>
              </tr>
            ))}
            {!isLoading && !runs?.length && (
              <tr><td className="td text-muted" colSpan={9}>No runs match these filters.</td></tr>
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
