import { formatDistanceToNow, parseISO } from 'date-fns'

import type { Phase } from '../types'

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
  // Terminal verdicts + stage outcomes.
  PASS: 'bg-pass/15 text-pass',
  FAIL: 'bg-fail/15 text-fail',
  ERROR: 'bg-fail/15 text-fail',
  // BLOCKED_BY_SAFETY is neither a pass nor a functional failure — surfaced loudly.
  BLOCKED_BY_SAFETY: 'bg-warn/20 text-warn',
  SKIPPED: 'bg-skip/20 text-muted',
  SKIP: 'bg-skip/20 text-muted',
  CANCELLED: 'bg-skip/20 text-muted',
  // Non-terminal verdicts + lifecycle phases (ADR-0005/0011).
  RUNNING: 'bg-info/15 text-info',
  QUEUED: 'bg-info/15 text-info',
  PENDING: 'bg-skip/20 text-muted',
  running: 'bg-info/15 text-info',
  queued: 'bg-info/15 text-info',
  terminal: 'bg-skip/20 text-muted',
  // Expectation / evaluator chips.
  DENY: 'bg-info/15 text-info',
  deterministic: 'bg-pass/15 text-pass',
  agentic: 'bg-warn/15 text-warn',
  UNKNOWN: 'bg-skip/20 text-muted',
}

export function statusClass(status?: string | null): string {
  return STATUS_CLASS[status ?? ''] ?? 'bg-skip/20 text-muted'
}

const TERMINAL_STATUSES = new Set([
  'PASS',
  'FAIL',
  'ERROR',
  'BLOCKED_BY_SAFETY',
  'SKIPPED',
  'CANCELLED',
])

/** Derive a Run's coarse lifecycle phase from its status (ADR-0005/0011): the
 *  same mapping the backend uses — `queued` while waiting on a busy target's
 *  lease, `terminal` once a verdict is available, else `running`. */
export function phaseOf(status?: string | null): Phase {
  if (status === 'QUEUED') return 'queued'
  if (status && TERMINAL_STATUSES.has(status)) return 'terminal'
  return 'running'
}

/** connectable/functional is a tri-state: null = no gating runs (not derivable). */
export function triState(value?: boolean | null): string {
  if (value == null) return 'n/a'
  return value ? 'yes' : 'no'
}

export function triStateClass(value?: boolean | null): string {
  if (value == null) return 'bg-skip/20 text-muted'
  return value ? 'bg-pass/15 text-pass' : 'bg-fail/15 text-fail'
}
