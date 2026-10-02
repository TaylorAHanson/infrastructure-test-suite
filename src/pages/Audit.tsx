import { useState } from 'react'
import { Download } from 'lucide-react'

import { PageHeader } from '../components/PageHeader'
import {
  auditExportUrl,
  useAudit,
  useAuditEventTypes,
  useTargets,
  type AuditFilters,
} from '../hooks/queries'
import { timeAgo } from '../lib/format'

// The audit log is the standing forensic control (ADR-0014): a read-only,
// filterable view of config-plane mutations only — runs are excluded by the API.
export default function Audit() {
  const [filters, setFilters] = useState<AuditFilters>({})
  const { data: events } = useAudit(filters)
  const { data: eventTypes } = useAuditEventTypes()
  const { data: targets } = useTargets()

  const set = (key: keyof AuditFilters) => (e: React.ChangeEvent<HTMLSelectElement | HTMLInputElement>) =>
    setFilters((f) => ({ ...f, [key]: e.target.value || undefined }))

  return (
    <div>
      <PageHeader
        title="Audit Log"
        subtitle="Append-only record of config-plane mutations (Lakebase). Runs are not audited — see Runs."
        actions={
          <>
            <a className="btn" href={auditExportUrl(filters, 'csv')}>
              <Download size="14" /> CSV
            </a>
            <a className="btn" href={auditExportUrl(filters, 'json')}>
              <Download size="14" /> JSON
            </a>
          </>
        }
      />

      <div className="card mb-4 flex flex-wrap items-center gap-3 p-3">
        <select className="input" value={filters.target ?? ''} onChange={set('target')}>
          <option value="">All targets</option>
          {targets?.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
        <select className="input" value={filters.event_type ?? ''} onChange={set('event_type')}>
          <option value="">All event types</option>
          {eventTypes?.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <input
          className="input flex-1"
          placeholder="Filter by actor…"
          value={filters.actor ?? ''}
          onChange={set('actor')}
        />
      </div>

      <div className="card overflow-x-auto p-0">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink">
            <tr>
              <th className="th">When</th>
              <th className="th">Actor</th>
              <th className="th">Kind</th>
              <th className="th">Event type</th>
              <th className="th">Entity</th>
              <th className="th">Summary</th>
            </tr>
          </thead>
          <tbody>
            {events?.map((e) => (
              <tr key={e.id} className="border-b border-edge/50 hover:bg-ink-soft">
                <td className="td text-muted">{timeAgo(e.ts)}</td>
                <td className="td text-white">{e.actor}</td>
                <td className="td text-muted">{e.actor_kind}</td>
                <td className="td"><span className="badge bg-ink-soft text-info">{e.action}</span></td>
                <td className="td text-muted">{e.entity_type}</td>
                <td className="td text-gray-200">{e.summary}</td>
              </tr>
            ))}
            {!events?.length && <tr><td className="td text-muted" colSpan={6}>No audit events match these filters.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
