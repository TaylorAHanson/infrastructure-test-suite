import { formatDistanceToNow, parseISO } from 'date-fns'

export function timeAgo(iso?: string | null): string {
  if (!iso) return '-'
  try {
    // Backend emits naive UTC; treat as UTC.
    const d = parseISO(iso.endsWith('Z') ? iso : `${iso}Z`)
    return formatDistanceToNow(d, { addSuffix: true })
  } catch {
    return iso
  }
}

export function duration(ms?: number | null): string {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function pct(value?: number | null): string {
  if (value == null) return '-'
  return `${(value * 100).toFixed(1)}%`
}

const STATUS_CLASS: Record<string, string> = {
  PASS: 'bg-pass/15 text-pass',
  PASSED: 'bg-pass/15 text-pass',
  CONNECTABLE: 'bg-pass/15 text-pass',
  FAIL: 'bg-fail/15 text-fail',
  FAILED: 'bg-fail/15 text-fail',
  ERROR: 'bg-fail/15 text-fail',
  NOT_CONNECTABLE: 'bg-fail/15 text-fail',
  PARTIAL: 'bg-warn/15 text-warn',
  RUNNING: 'bg-info/15 text-info',
  QUEUED: 'bg-info/15 text-info',
  PENDING: 'bg-skip/20 text-muted',
  SKIP: 'bg-skip/20 text-muted',
  UNKNOWN: 'bg-skip/20 text-muted',
}

export function statusClass(status?: string | null): string {
  return STATUS_CLASS[status ?? ''] ?? 'bg-skip/20 text-muted'
}

export const CATEGORY_LABEL: Record<string, string> = {
  PRODUCER: 'Producer',
  PROCESSING: 'Processing',
  CONSUMER: 'Consumer',
}
