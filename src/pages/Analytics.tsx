import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { PageHeader } from '../components/PageHeader'
import { useAnalytics } from '../hooks/queries'
import { duration, pct } from '../lib/format'

interface PassRow { key: string; total: number; pass_rate: number }
interface DurRow { name: string; mean_ms: number; p95_ms: number }
interface ConnVsFunc { total_failures: number; connectivity_failures: number; functional_failures: number }

export default function Analytics() {
  const { data: byCat } = useAnalytics<PassRow[]>('pass-rate-by', '?dimension=category')
  const { data: byTarget } = useAnalytics<PassRow[]>('pass-rate-by', '?dimension=target')
  const { data: cvf } = useAnalytics<ConnVsFunc>('connectivity-vs-functional')
  const { data: durations } = useAnalytics<DurRow[]>('duration-by-test')
  const { data: pl } = useAnalytics<Record<string, number>>('privatelink-failures')

  const plRows = Object.entries(pl ?? {}).map(([k, v]) => ({ name: k, value: v }))

  return (
    <div>
      <PageHeader title="Failure Analytics" subtitle="Connectivity vs functional, PrivateLink views, durations & flakiness" />

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card">
          <div className="card-title">Total failures</div>
          <div className="stat text-fail">{cvf?.total_failures ?? '-'}</div>
        </div>
        <div className="card">
          <div className="card-title">Connectivity failures</div>
          <div className="stat text-warn">{cvf?.connectivity_failures ?? '-'}</div>
        </div>
        <div className="card">
          <div className="card-title">Functional failures</div>
          <div className="stat text-info">{cvf?.functional_failures ?? '-'}</div>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <div className="card-title mb-3">PrivateLink-specific failures</div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={plRows} layout="vertical" margin={{ left: 50 }}>
              <XAxis type="number" stroke="#8a97ad" fontSize={11} />
              <YAxis type="category" dataKey="name" stroke="#8a97ad" fontSize={10} width={150} />
              <Tooltip contentStyle={{ background: '#111a2b', border: '1px solid #22304a' }} />
              <Bar dataKey="value" fill="#d29922" />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <div className="card-title mb-3">Pass rate by category</div>
          <table className="w-full">
            <tbody>
              {byCat?.map((r) => (
                <tr key={r.key} className="border-b border-edge/50">
                  <td className="td">{r.key}</td>
                  <td className="td text-right text-muted">{r.total} runs</td>
                  <td className="td text-right text-pass">{pct(r.pass_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="card-title mb-2 mt-4">Pass rate by target</div>
          <table className="w-full">
            <tbody>
              {byTarget?.map((r) => (
                <tr key={r.key} className="border-b border-edge/50">
                  <td className="td">{r.key}</td>
                  <td className="td text-right text-muted">{r.total} runs</td>
                  <td className="td text-right text-pass">{pct(r.pass_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="card-title mb-3">Duration by test (mean / p95)</div>
        <table className="w-full">
          <thead>
            <tr>
              <th className="th">Test</th>
              <th className="th text-right">Mean</th>
              <th className="th text-right">p95</th>
            </tr>
          </thead>
          <tbody>
            {durations?.slice(0, 15).map((d) => (
              <tr key={d.name} className="border-b border-edge/50">
                <td className="td">{d.name}</td>
                <td className="td text-right text-muted">{duration(d.mean_ms)}</td>
                <td className="td text-right text-muted">{duration(d.p95_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
