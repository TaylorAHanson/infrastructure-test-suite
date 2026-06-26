import { Link } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { PageHeader } from '../components/PageHeader'
import { RunLauncher } from '../components/RunLauncher'
import { useAnalytics } from '../hooks/queries'
import { duration, pct } from '../lib/format'

interface Overview {
  total_runs: number
  pass_rate: number | null
  fail_count: number
  median_duration_ms: number | null
  connectable_rate: number | null
}
interface TrendPoint {
  date: string
  failure_rate: number
  connectivity_failures: number
  functional_failures: number
}
interface FailingRow { name: string; failures: number; total: number; failure_rate: number }
interface Bucket { bucket: string; count: number; is_connectivity: boolean }

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="card">
      <div className="card-title">{label}</div>
      <div className={`stat ${accent ?? ''}`}>{value}</div>
    </div>
  )
}

export default function Dashboard() {
  const { data: ov } = useAnalytics<Overview>('overview')
  const { data: trend } = useAnalytics<TrendPoint[]>('failure-rate-over-time')
  const { data: topTests } = useAnalytics<FailingRow[]>('top-failing', '?dimension=test&limit=6')
  const { data: topTargets } = useAnalytics<FailingRow[]>('top-failing', '?dimension=target&limit=6')
  const { data: buckets } = useAnalytics<Bucket[]>('root-cause-buckets')
  const { data: flaky } = useAnalytics<{ name: string; flip_rate: number }[]>('flaky-tests')

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Platform regression & connectivity health (last 30 days)" />

      <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-5">
        <Stat label="Total runs" value={String(ov?.total_runs ?? '-')} />
        <Stat label="Pass rate" value={pct(ov?.pass_rate)} accent="text-pass" />
        <Stat label="Connectable rate" value={pct(ov?.connectable_rate)} accent="text-info" />
        <Stat label="Failures" value={String(ov?.fail_count ?? '-')} accent="text-fail" />
        <Stat label="Median duration" value={duration(ov?.median_duration_ms)} />
      </div>

      <div className="mb-4">
        <RunLauncher />
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <div className="card-title mb-3">Failure rate over time</div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={trend ?? []}>
              <CartesianGrid stroke="#22304a" strokeDasharray="3 3" />
              <XAxis dataKey="date" stroke="#8a97ad" fontSize={11} />
              <YAxis stroke="#8a97ad" fontSize={11} />
              <Tooltip contentStyle={{ background: '#111a2b', border: '1px solid #22304a' }} />
              <Line type="monotone" dataKey="failure_rate" stroke="#f85149" dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="connectivity_failures" stroke="#d29922" dot={false} />
              <Line type="monotone" dataKey="functional_failures" stroke="#58a6ff" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <div className="card-title mb-3">Top root-cause buckets</div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={(buckets ?? []).slice(0, 8)} layout="vertical" margin={{ left: 40 }}>
              <XAxis type="number" stroke="#8a97ad" fontSize={11} />
              <YAxis type="category" dataKey="bucket" stroke="#8a97ad" fontSize={9} width={170} />
              <Tooltip contentStyle={{ background: '#111a2b', border: '1px solid #22304a' }} />
              <Bar dataKey="count">
                {(buckets ?? []).slice(0, 8).map((b, i) => (
                  <Cell key={i} fill={b.is_connectivity ? '#d29922' : '#58a6ff'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <FailingList title="Top failing tests" rows={topTests} />
        <FailingList title="Top failing targets" rows={topTargets} />
        <div className="card">
          <div className="card-title mb-3">Flaky tests</div>
          <ul className="space-y-2">
            {(flaky ?? []).slice(0, 6).map((f) => (
              <li key={f.name} className="flex items-center justify-between text-sm">
                <span className="truncate text-gray-200">{f.name}</span>
                <span className="text-warn">{pct(f.flip_rate)}</span>
              </li>
            ))}
            {!flaky?.length && <li className="text-sm text-muted">No flaky tests detected.</li>}
          </ul>
        </div>
      </div>
    </div>
  )
}

function FailingList({ title, rows }: { title: string; rows?: FailingRow[] }) {
  return (
    <div className="card">
      <div className="card-title mb-3">{title}</div>
      <ul className="space-y-2">
        {(rows ?? []).map((r) => (
          <li key={r.name} className="flex items-center justify-between text-sm">
            <span className="truncate text-gray-200">{r.name}</span>
            <span className="text-fail">
              {r.failures}/{r.total} ({pct(r.failure_rate)})
            </span>
          </li>
        ))}
        {!rows?.length && (
          <li className="text-sm text-muted">
            No failures yet. <Link to="/runs" className="text-accent">Trigger a run</Link>.
          </li>
        )}
      </ul>
    </div>
  )
}
