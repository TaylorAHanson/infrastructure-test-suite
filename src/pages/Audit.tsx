import { PageHeader } from '../components/PageHeader'
import { useAudit } from '../hooks/queries'
import { timeAgo } from '../lib/format'

export default function Audit() {
  const { data: events } = useAudit()
  return (
    <div>
      <PageHeader title="Audit Log" subtitle="Durable record of every action (persisted in Lakebase, not just app logs)" />
      <div className="card overflow-hidden p-0">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink">
            <tr>
              <th className="th">When</th>
              <th className="th">Actor</th>
              <th className="th">Kind</th>
              <th className="th">Action</th>
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
            {!events?.length && <tr><td className="td text-muted" colSpan={6}>No audit events yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
