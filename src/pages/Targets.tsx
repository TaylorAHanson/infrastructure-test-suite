import { PageHeader } from '../components/PageHeader'
import { useCatalogs, useTargets } from '../hooks/queries'

export default function Targets() {
  const { data: targets } = useTargets()
  const { data: catalogs } = useCatalogs()

  return (
    <div>
      <PageHeader title="Targets & Catalogs" subtitle="Environments probes run against; drives all/partial catalog execution" />

      <div className="card mb-4">
        <div className="card-title mb-2">Catalogs</div>
        <div className="flex flex-wrap gap-2">
          {catalogs?.map((c) => <span key={c} className="badge bg-ink-soft text-info">{c}</span>)}
          {!catalogs?.length && <span className="text-sm text-muted">No catalogs configured.</span>}
        </div>
      </div>

      <div className="card overflow-hidden p-0">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink">
            <tr>
              <th className="th">Name</th>
              <th className="th">Type</th>
              <th className="th">Catalog</th>
              <th className="th">Schema</th>
              <th className="th">Workspace</th>
              <th className="th">Cloud / Region</th>
            </tr>
          </thead>
          <tbody>
            {targets?.map((t) => (
              <tr key={t.id} className="border-b border-edge/50 hover:bg-ink-soft">
                <td className="td text-white">{t.name}</td>
                <td className="td text-muted">{t.target_type}</td>
                <td className="td text-muted">{t.catalog ?? '-'}</td>
                <td className="td text-muted">{t.schema_name ?? '-'}</td>
                <td className="td text-muted">{t.workspace_name ?? '-'}</td>
                <td className="td text-muted">{t.cloud} / {t.region ?? '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
