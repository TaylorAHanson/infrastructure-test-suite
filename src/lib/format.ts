import { formatDistanceToNow, parseISO } from 'date-fns'

import type { Phase, RunRequestSummary, StudioTest, Suite, Target } from '../types'

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

/** A run's git sha, or null when the build didn't record one. */
export function knownSha(sha?: string | null): string | null {
  return sha && sha !== 'unknown' ? sha : null
}

/** A target picker's explicit "All targets" choice (sent as an empty `target_ids`). */
export const ALL_TARGETS = '__all__'

/** "aws-prod", "aws-prod, sandbox-1", "5 targets" or "all targets". */
export function targetsLabel(ids: string[], targets?: Target[]): string {
  if (!ids.length) return 'all targets'
  if (ids.length > 2) return `${ids.length} targets`
  return ids.map((id) => targets?.find((t) => t.id === id)?.name ?? id).join(', ')
}

/** What a run selected, by name: its suites and tests, or every Active Test. */
export function runScopeLabel(
  run: Pick<RunRequestSummary, 'suite_ids' | 'test_ids'>,
  suites?: Suite[],
  tests?: StudioTest[],
): string {
  const names = [
    ...run.suite_ids.map((id) => suites?.find((s) => s.id === id)?.name ?? id),
    ...run.test_ids.map((id) => tests?.find((t) => t.id === id)?.name ?? id),
  ]
  if (!names.length) return 'All active tests'
  return names.length > 3 ? `${names.slice(0, 2).join(', ')} +${names.length - 2} more` : names.join(', ')
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
