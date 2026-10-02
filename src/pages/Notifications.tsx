import { useEffect, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { useUpdateWebhookConfig, useWebhookConfig } from '../hooks/queries'

// The run-completion webhook sink (ADR-0016): the platform's only push. A
// single generic outbound POST fires on a completed run with ≥ 1 finding
// (silent on all-green; the toggle widens to every completion). Editable by any
// authenticated human with no ceremony — but every edit is audited.
export default function Notifications() {
  const { data } = useWebhookConfig()
  const update = useUpdateWebhookConfig()

  const [urls, setUrls] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [fireAll, setFireAll] = useState(false)

  // Hydrate the form once the config loads (and after a save re-fetches it).
  useEffect(() => {
    if (!data) return
    setUrls((data.urls ?? []).join('\n'))
    setEnabled(data.enabled)
    setFireAll(data.fire_on_all_completions)
  }, [data])

  const save = () => {
    update.mutate({
      urls: urls.split('\n').map((u) => u.trim()).filter(Boolean),
      enabled,
      fire_on_all_completions: fireAll,
    })
  }

  return (
    <div>
      <PageHeader
        title="Notifications"
        subtitle="A run-completion webhook: one generic POST to your own receiver (Slack, PagerDuty, an email relay…)."
      />
      <div className="card">
        <p className="mb-3 text-sm text-muted">
          Fires when a completed run produces at least one finding and stays silent on all-green runs,
          unless you widen it to every completion. Delivery is best-effort; edits are audited.
        </p>
        <label className="mb-1 block text-xs text-muted">Sink URL(s) — one per line</label>
        <textarea
          className="input mb-3 h-20 w-full font-mono text-xs"
          placeholder="https://hooks.example.com/services/…"
          value={urls}
          onChange={(e) => setUrls(e.target.value)}
        />
        <div className="mb-3 flex flex-wrap gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            Enabled
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={fireAll} onChange={(e) => setFireAll(e.target.checked)} />
            Fire on every completion (not just findings)
          </label>
        </div>
        <div className="flex items-center gap-3">
          <button className="btn btn-accent" onClick={save} disabled={update.isPending}>
            {update.isPending ? 'Saving…' : 'Save sink config'}
          </button>
          {update.isSuccess && <span className="text-xs text-info">Saved.</span>}
          {update.isError && <span className="text-xs text-warn">{String(update.error)}</span>}
        </div>
      </div>
    </div>
  )
}
