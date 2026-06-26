import { useMemo, useState } from 'react'

import { Badge } from '../components/Badge'
import { PageHeader } from '../components/PageHeader'
import { useTests } from '../hooks/queries'

export default function TestCatalog() {
  const { data: tests } = useTests()
  const [q, setQ] = useState('')
  const [cat, setCat] = useState('')

  const filtered = useMemo(() => {
    return (tests ?? []).filter((t) => {
      const matchesQ =
        !q ||
        t.display_name.toLowerCase().includes(q.toLowerCase()) ||
        t.tags.some((tag) => tag.includes(q.toLowerCase()))
      const matchesCat = !cat || t.category === cat
      return matchesQ && matchesCat
    })
  }, [tests, q, cat])

  return (
    <div>
      <PageHeader
        title="Test Catalog"
        subtitle="Metadata-driven probe registry. New probes are added by configuration + a small executor plugin."
      />
      <div className="card mb-4 flex flex-wrap gap-3">
        <input className="input flex-1" placeholder="Search by name or tag..." value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input" value={cat} onChange={(e) => setCat(e.target.value)}>
          <option value="">All categories</option>
          <option value="PRODUCER">Producer</option>
          <option value="PROCESSING">Processing</option>
          <option value="CONSUMER">Consumer</option>
        </select>
      </div>

      <div className="card overflow-hidden p-0">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink">
            <tr>
              <th className="th">Probe</th>
              <th className="th">Category</th>
              <th className="th">Executor</th>
              <th className="th">Modes</th>
              <th className="th">Target type</th>
              <th className="th">Flags</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => (
              <tr key={t.id} className="border-b border-edge/50 hover:bg-ink-soft">
                <td className="td">
                  <div className="text-white">{t.display_name}</div>
                  <div className="text-xs text-muted">{t.tags.join(' · ')}</div>
                </td>
                <td className="td text-muted">{t.category}</td>
                <td className="td">
                  <div className="font-mono text-xs text-info">{t.executor_key}</div>
                  <div className="text-xs text-muted">{t.executor_kind}</div>
                </td>
                <td className="td">
                  <div className="flex gap-1">
                    {t.supported_execution_modes.map((m) => (
                      <span key={m} className="badge bg-ink-soft text-muted">{m}</span>
                    ))}
                  </div>
                </td>
                <td className="td text-muted">{t.target_type}</td>
                <td className="td">
                  <div className="flex flex-wrap gap-1">
                    {t.destructive && <span className="badge bg-fail/15 text-fail">destructive</span>}
                    {t.enterprise_only && <span className="badge bg-warn/15 text-warn">enterprise</span>}
                    {!t.enabled && <Badge value="SKIP" />}
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
