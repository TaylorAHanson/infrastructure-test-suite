import { PageHeader } from '../components/PageHeader'
import { useAdminSettings } from '../hooks/queries'

// Remote agents were dropped as out of scope, so this page no longer calls the
// removed /agents endpoint. It renders only what /admin/settings actually
// returns: the registered handlers, the canonical stage spine, the (extensible)
// failure taxonomy, and catalog counts.
interface CanonicalStage {
  id: string
  label: string
  connectivity: boolean
}
interface FailureBucket {
  id: string
  connectivity: boolean
  stage?: string | null
}
interface AdminSettings {
  registered_handler_keys?: string[]
  canonical_stages?: CanonicalStage[]
  failure_taxonomy?: FailureBucket[]
  catalog_counts?: Record<string, number>
}

const COUNT_LABEL: Record<string, string> = {
  checks: 'Checks',
  tests: 'Tests',
  suites: 'Suites',
  targets: 'Targets',
  check_runs: 'CheckRuns',
}

export default function Admin() {
  const { data } = useAdminSettings()
  const s = (data ?? {}) as AdminSettings
  const counts = s.catalog_counts ?? {}

  return (
    <div>
      <PageHeader title="Admin & Settings" subtitle="Registered handlers, the canonical stage spine, the failure taxonomy, and catalog counts" />

      <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-5">
        {Object.entries(COUNT_LABEL).map(([key, label]) => (
          <div key={key} className="card py-3">
            <div className="card-title">{label}</div>
            <div className="stat">{counts[key] ?? '-'}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <div className="card-title mb-2">Registered handlers</div>
          <div className="flex flex-wrap gap-2">
            {(s.registered_handler_keys ?? []).map((k) => (
              <span key={k} className="badge bg-ink-soft text-info">{k}</span>
            ))}
            {!s.registered_handler_keys?.length && <span className="text-sm text-muted">none</span>}
          </div>
        </div>

        <div className="card">
          <div className="card-title mb-2">Canonical stage spine</div>
          <ol className="flex flex-wrap items-center gap-1 text-xs">
            {(s.canonical_stages ?? []).map((st, i) => (
              <li key={st.id} className="flex items-center gap-1">
                <span className={`badge ${st.connectivity ? 'bg-info/15 text-info' : 'bg-warn/15 text-warn'}`}>
                  {st.label ?? st.id}
                </span>
                {i < (s.canonical_stages?.length ?? 0) - 1 && <span className="text-muted">→</span>}
              </li>
            ))}
          </ol>
          <p className="mt-2 text-xs text-muted">
            Blue = connectivity stage (failure here ≤ AUTHORIZE); amber = functional (OPERATE / VERIFY).
          </p>
        </div>

        <div className="card lg:col-span-2">
          <div className="card-title mb-2">Failure taxonomy</div>
          <div className="overflow-hidden rounded-lg border border-edge">
            <table className="w-full">
              <thead className="bg-ink">
                <tr>
                  <th className="th">Bucket</th>
                  <th className="th">Stage</th>
                  <th className="th">Kind</th>
                </tr>
              </thead>
              <tbody>
                {(s.failure_taxonomy ?? []).map((b) => (
                  <tr key={b.id} className="border-t border-edge/50">
                    <td className="td font-mono text-xs text-gray-200">{b.id}</td>
                    <td className="td text-xs text-muted">{b.stage ?? '-'}</td>
                    <td className="td">
                      <span className={`badge ${b.connectivity ? 'bg-info/15 text-info' : 'bg-warn/15 text-warn'}`}>
                        {b.connectivity ? 'connectivity' : 'functional'}
                      </span>
                    </td>
                  </tr>
                ))}
                {!s.failure_taxonomy?.length && (
                  <tr><td className="td text-sm text-muted" colSpan={3}>No taxonomy loaded.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
