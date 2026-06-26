import { Play, Power } from 'lucide-react'

import { PageHeader } from '../components/PageHeader'
import { useFireSchedule, useSchedules, useToggleSchedule } from '../hooks/queries'
import { timeAgo } from '../lib/format'

export default function Schedules() {
  const { data: schedules } = useSchedules()
  const toggle = useToggleSchedule()
  const fire = useFireSchedule()

  return (
    <div>
      <PageHeader
        title="Schedules"
        subtitle="Cron schedules materialise as Databricks Jobs; each fire creates a run request"
      />
      <div className="card overflow-hidden p-0">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink">
            <tr>
              <th className="th">Name</th>
              <th className="th">Cron</th>
              <th className="th">Mode</th>
              <th className="th">Databricks Job</th>
              <th className="th">Last fired</th>
              <th className="th">Enabled</th>
              <th className="th">Actions</th>
            </tr>
          </thead>
          <tbody>
            {schedules?.map((s) => (
              <tr key={s.id} className="border-b border-edge/50 hover:bg-ink-soft">
                <td className="td text-white">{s.name}</td>
                <td className="td font-mono text-xs text-muted">{s.cron}</td>
                <td className="td text-muted">{s.execution_mode}</td>
                <td className="td text-muted">{s.databricks_job_id ?? 'bundle-managed'}</td>
                <td className="td text-muted">{timeAgo(s.last_fired_at)}</td>
                <td className="td">
                  <span className={`badge ${s.enabled ? 'bg-pass/15 text-pass' : 'bg-skip/20 text-muted'}`}>
                    {s.enabled ? 'enabled' : 'disabled'}
                  </span>
                </td>
                <td className="td">
                  <div className="flex gap-2">
                    <button className="btn" onClick={() => fire.mutate(s.id)}>
                      <Play size="14" /> Fire
                    </button>
                    <button className="btn" onClick={() => toggle.mutate({ id: s.id, enabled: !s.enabled })}>
                      <Power size="14" /> {s.enabled ? 'Disable' : 'Enable'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
