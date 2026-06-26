import { PageHeader } from '../components/PageHeader'
import { useSuites, useTests } from '../hooks/queries'

export default function Suites() {
  const { data: suites } = useSuites()
  const { data: tests } = useTests()
  const nameOf = (id: string) => tests?.find((t) => t.id === id)?.display_name ?? id

  return (
    <div>
      <PageHeader title="Suites" subtitle="Group probes for full or partial suite execution" />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {suites?.map((s) => (
          <div key={s.id} className="card">
            <div className="flex items-center justify-between">
              <div className="text-base font-semibold text-white">{s.name}</div>
              <span className="badge bg-ink-soft text-muted">{s.test_definition_ids.length} probes</span>
            </div>
            <p className="mt-1 text-sm text-muted">{s.description}</p>
            <ul className="mt-3 space-y-1">
              {s.test_definition_ids.slice(0, 12).map((id) => (
                <li key={id} className="text-sm text-gray-200">· {nameOf(id)}</li>
              ))}
              {s.test_definition_ids.length > 12 && (
                <li className="text-xs text-muted">+ {s.test_definition_ids.length - 12} more</li>
              )}
            </ul>
          </div>
        ))}
      </div>
    </div>
  )
}
