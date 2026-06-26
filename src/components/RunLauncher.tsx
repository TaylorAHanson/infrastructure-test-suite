import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Play } from 'lucide-react'

import { useCatalogs, useSubmitRun, useSuites } from '../hooks/queries'

// Supports full-suite or partial-suite execution and all-catalogs or
// partial-catalog execution, across the three execution modes.
export function RunLauncher({ onLaunched }: { onLaunched?: (id: string) => void }) {
  const navigate = useNavigate()
  const { data: suites } = useSuites()
  const { data: catalogs } = useCatalogs()
  const submit = useSubmitRun()

  const [suiteId, setSuiteId] = useState('')
  const [catalog, setCatalog] = useState('')
  const [mode, setMode] = useState('SMOKE')
  const [error, setError] = useState<string | null>(null)

  async function launch() {
    setError(null)
    try {
      const res = await submit.mutateAsync({
        suite_ids: suiteId ? [suiteId] : [],
        catalogs: catalog ? [catalog] : [],
        execution_mode: mode,
        requested_by: 'ui-user',
      })
      if (onLaunched) onLaunched(res.id)
      else navigate('/runs')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to submit run.')
    }
  }

  return (
    <div className="card flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-1">
        <label className="card-title">Suite (blank = full)</label>
        <select className="input" value={suiteId} onChange={(e) => setSuiteId(e.target.value)}>
          <option value="">All tests</option>
          {suites?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="card-title">Catalog (blank = all)</label>
        <select className="input" value={catalog} onChange={(e) => setCatalog(e.target.value)}>
          <option value="">All catalogs</option>
          {catalogs?.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="card-title">Mode</label>
        <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="PREFLIGHT">Preflight (connectable only)</option>
          <option value="SMOKE">Smoke (minimal functional)</option>
          <option value="FULL">Full (end-to-end)</option>
        </select>
      </div>
      <button className="btn btn-accent" onClick={launch} disabled={submit.isPending}>
        <Play size="16" />
        {submit.isPending ? 'Submitting...' : 'Run'}
      </button>
      {submit.isSuccess && !error && (
        <span className="text-sm text-pass">Run submitted - opening Runs...</span>
      )}
      {error && <span className="w-full text-sm text-fail">{error}</span>}
    </div>
  )
}
