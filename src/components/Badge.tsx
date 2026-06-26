import { statusClass } from '../lib/format'

export function Badge({ value }: { value?: string | null }) {
  if (!value) return <span className="badge bg-skip/20 text-muted">-</span>
  return <span className={`badge ${statusClass(value)}`}>{value}</span>
}
