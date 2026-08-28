import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Play } from 'lucide-react'

import { useSubmitRun, useSuites, useTargets } from '../hooks/queries'

// Launch a Run against the rebuilt Run API (ADR-0005): pick a Suite (blank =
// every Active Test), a Target (blank = all registered targets — the per-target
// safety gate still fires per target), and a depth. Depth defaults to `quick`;
// `full` is an explicit opt-in (ADR-0007).
export function RunLauncher({ onLaunched }: { onLaunched?: (id: string) => void }) {
  const navigate = useNavigate()
  const { data: suites } = useSuites()
  const { data: targets } = useTargets()
  const submit = useSubmitRun()

  const [suiteId, setSuiteId] = useState('')
  const [targetId, setTargetId] = useState('')
  const [depth, setDepth] = useState<'quick' | 'full'>('quick')
  const [error, setError] = useState<string | null>(null)

  async function launch() {
    setError(null)
    try {
      const res = await submit.mutateAsync({
        suite_ids: suiteId ? [suiteId] : [],
        target_ids: targetId ? [targetId] : [],
        depth,
        requested_by: 'ui-user',
        request_source: 'UI_MANUAL',
      })
      if (onLaunched) onLaunched(res.run_id)
      else navigate(`/runs/${res.run_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to submit run.')
    }
  }

  return (
    <div className="card flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-1">
        <label className="card-title">Suite (blank = all Active Tests)</label>
        <select className="input" value={suiteId} onChange={(e) => setSuiteId(e.target.value)}>
          <option value="">All Tests</option>
          {suites?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="card-title">Target (blank = all)</label>
        <select className="input" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
          <option value="">All targets</option>
          {targets?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="card-title">Depth</label>
        <select
          className="input"
          value={depth}
          onChange={(e) => setDepth(e.target.value as 'quick' | 'full')}
        >
          <option value="quick">Quick (connectivity end)</option>
          <option value="full">Full (through OPERATE / VERIFY)</option>
        </select>
      </div>
      <button className="btn btn-accent" onClick={launch} disabled={submit.isPending}>
        <Play size="16" />
        {submit.isPending ? 'Submitting...' : 'Run'}
      </button>
      {submit.isSuccess && !error && (
        <span className="text-sm text-pass">Run submitted - opening run...</span>
      )}
      {error && <span className="w-full text-sm text-fail">{error}</span>}
    </div>
  )
}
