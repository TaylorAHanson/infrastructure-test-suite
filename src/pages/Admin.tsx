import { PageHeader } from '../components/PageHeader'
import { useAdminSettings, useAgents } from '../hooks/queries'

function Section({ title, obj }: { title: string; obj: unknown }) {
  return (
    <div className="card">
      <div className="card-title mb-2">{title}</div>
      <pre className="overflow-auto rounded-lg bg-ink p-3 font-mono text-[11px] text-gray-300">
        {JSON.stringify(obj, null, 2)}
      </pre>
    </div>
  )
}

export default function Admin() {
  const { data: settings } = useAdminSettings()
  const { data: agents } = useAgents()
  const s = (settings ?? {}) as Record<string, unknown>

  return (
    <div>
      <PageHeader title="Admin & Settings" subtitle="Resource bindings, concurrency/timeout policy, telemetry, feature flags" />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Section title="Databricks resource bindings" obj={s.resource_bindings} />
        <Section title="Concurrency & timeout policy" obj={s.concurrency_policy} />
        <Section title="Telemetry" obj={s.telemetry} />
        <Section title="Feature flags" obj={s.feature_flags} />
        <div className="card">
          <div className="card-title mb-2">Remote agents</div>
          {agents?.length ? (
            <ul className="space-y-2">
              {agents.map((a) => (
                <li key={a.id} className="flex items-center justify-between text-sm">
                  <span className="text-white">{a.name} <span className="text-muted">({a.vantage_point})</span></span>
                  <span className="text-muted">{a.status}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">
              No remote agents registered. Register agents at external vantage points
              (Power BI / Tableau / on-prem) to prove outside-in connectivity.
            </p>
          )}
        </div>
        <Section title="Registered executor plugins" obj={s.registered_executor_keys} />
      </div>
    </div>
  )
}
