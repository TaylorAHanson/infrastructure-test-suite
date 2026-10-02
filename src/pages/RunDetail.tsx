import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ChevronDown, ChevronRight } from 'lucide-react'

import { Badge } from '../components/Badge'
import { PageHeader } from '../components/PageHeader'
import { useCheckRun, useRunCheckRuns, useRunResource, useSuites, useTargets, useTests } from '../hooks/queries'
import { errorMessage } from '../lib/api'
import {
  duration, knownSha, runScopeLabel, targetsLabel, timeAgo, triState, triStateClass,
} from '../lib/format'
import type { CheckRunRow } from '../types'

export default function RunDetail() {
  const { runId } = useParams()
  const { data: run, isLoading, error } = useRunResource(runId)
  const terminal = run?.phase === 'terminal'
  const checkRunsQ = useRunCheckRuns(run ? runId : undefined, terminal)
  const { data: tests } = useTests()
  const { data: suites } = useSuites()
  const { data: targets } = useTargets()

  if (!run && error) {
    return (
      <div className="card text-sm text-muted">
        <span className="text-fail">Couldn't load this run: {errorMessage(error)}</span>{' '}
        <Link to="/runs" className="text-accent hover:underline">Back to runs</Link>
      </div>
    )
  }
  if (isLoading || !run) return <div className="text-muted">Loading run...</div>

  const testName = (id: string) => tests?.find((t) => t.id === id)?.name ?? id
  const rollup = Object.entries(run.test_rollup ?? {})
  const gating = Object.entries(run.failure_summary?.gating ?? {})
  const advisory = Object.entries(run.failure_summary?.advisory ?? {})
  const sha = knownSha(run.git_sha)

  const seen = new Set<string>()
  const checkRuns = (checkRunsQ.data?.pages.flat() ?? []).filter((cr) => !seen.has(cr.run_id) && !!seen.add(cr.run_id))
  const total = Math.max(run.run_ids.length, checkRuns.length)

  return (
    <div>
      <Link to="/runs" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-white">
        <ArrowLeft size="14" /> Back to runs
      </Link>
      <PageHeader
        title="Run"
        subtitle={run.run_id}
        actions={<><Badge value={run.phase} /><Badge value={run.verdict} /></>}
      />

      <div className="card mb-4 grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-3 md:divide-x md:divide-edge">
        <MetaGroup title="Request">
          <Meta label="Source" value={run.request_source} />
          <Meta label="Requested by" value={run.requested_by} />
          <Meta label="Created" value={timeAgo(run.created_at)} title={run.created_at} />
        </MetaGroup>
        <MetaGroup title="Scope">
          <Meta label="Ran" value={runScopeLabel(run, suites, tests)} />
          <Meta label="Depth" value={run.depth} />
          <Meta label="Targets" value={targetsLabel(run.target_ids, targets)} />
          <Meta label="Check runs" value={String(run.run_ids.length)} />
        </MetaGroup>
        <MetaGroup title="Result">
          <MetaBadge label="Connectable" value={triState(run.connectable)} cls={triStateClass(run.connectable)} />
          <MetaBadge label="Functional" value={triState(run.functional)} cls={triStateClass(run.functional)} />
          <Meta label="Build" value={sha ? `v${run.app_version} · ${sha}` : `v${run.app_version}`} mono />
        </MetaGroup>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Per-Test roll-up (gating-only, fail-closed). */}
        <div className="card">
          <div className="card-title mb-2">Per-Test roll-up</div>
          {rollup.length ? (
            <ul className="space-y-1">
              {rollup.map(([tid, verdict]) => (
                <li key={tid} className="flex items-center justify-between text-sm">
                  <span className="text-gray-200">{testName(tid)}</span>
                  <Badge value={verdict} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">No Tests in this run.</p>
          )}
        </div>

        {/* Failure-taxonomy summary: gating (blocks) vs advisory (signal only). */}
        <div className="card">
          <div className="card-title mb-2">Failure summary</div>
          <div className="text-xs uppercase tracking-wide text-muted">Gating (sets the verdict)</div>
          {gating.length ? (
            <ul className="mb-3 mt-1 space-y-1">
              {gating.map(([bucket, count]) => (
                <li key={bucket} className="flex items-center justify-between text-sm">
                  <span className="font-mono text-fail">{bucket}</span>
                  <span className="text-muted">{count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mb-3 mt-1 text-sm text-pass">None</p>
          )}
          <div className="text-xs uppercase tracking-wide text-muted">Advisory (doesn't affect the verdict)</div>
          {advisory.length ? (
            <ul className="mt-1 space-y-1">
              {advisory.map(([bucket, count]) => (
                <li key={bucket} className="flex items-center justify-between text-sm">
                  <span className="font-mono text-warn">{bucket}</span>
                  <span className="text-muted">{count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm text-muted">None</p>
          )}
        </div>
      </div>

      <div className="mb-2 flex items-baseline gap-2">
        <span className="card-title">Check results</span>
        {checkRuns.length > 0 && checkRuns.length < total && (
          <span className="text-xs text-muted">Showing {checkRuns.length} of {total}</span>
        )}
      </div>
      <div className="space-y-2">
        {checkRuns.map((cr) => <CheckRunRowView key={cr.run_id} row={cr} />)}
        {checkRunsQ.isPending && <div className="card text-sm text-muted">Loading check results…</div>}
        {checkRunsQ.error && !checkRuns.length && (
          <div className="card text-sm text-fail">Couldn't load the check results: {errorMessage(checkRunsQ.error)}</div>
        )}
        {checkRunsQ.isSuccess && !checkRuns.length && (
          <div className="card text-sm text-muted">
            {run.phase === 'terminal'
              ? 'No checks ran in this run.'
              : run.phase === 'queued'
                ? 'No check results yet — the run is queued on a target lease.'
                : 'No check results yet — the run is still running.'}
          </div>
        )}
      </div>
      {checkRuns.length > 0 && checkRuns.length < total && (
        <div className="mt-3 flex items-center gap-3 text-sm text-muted">
          Showing {checkRuns.length} of {total} check results.
          {checkRunsQ.hasNextPage && (
            <button
              className="btn py-1 text-xs"
              onClick={() => checkRunsQ.fetchNextPage()}
              disabled={checkRunsQ.isFetchingNextPage}
            >
              {checkRunsQ.isFetchingNextPage ? 'Loading…' : 'Load more'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// One CheckRun row, expandable to its stage outcomes on the canonical spine.
// Stage outcomes (incl. a BLOCKED_BY_SAFETY result) are fetched lazily on expand.
function CheckRunRowView({ row }: { row: CheckRunRow }) {
  const [open, setOpen] = useState(false)
  const { data: detail } = useCheckRun(row.run_id, open)

  return (
    <div className="card">
      <button
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((o) => !o)}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          {open ? <ChevronDown size="14" className="shrink-0" /> : <ChevronRight size="14" className="shrink-0" />}
          <span className="text-sm text-white">{row.check_name}</span>
          <Badge value={row.verdict} />
          {!row.gating && (
            <span className="badge bg-flaky/15 text-flaky" title="Reported, but never changes the run's verdict">advisory</span>
          )}
          {row.expectation === 'DENY' && <span className="badge bg-info/15 text-info">counterfactual</span>}
          {row.bucket_at_stage && <span className="font-mono text-xs text-fail">{row.bucket_at_stage}</span>}
        </div>
        <div className="flex shrink-0 items-center gap-3 text-xs text-muted">
          <span>{row.workspace_name ?? 'unbound'}</span>
          <span>{duration(row.duration_ms)}</span>
        </div>
      </button>

      {row.message && <div className="mt-2 pl-6 text-xs text-gray-300">{row.message}</div>}

      {open && (
        <div className="mt-3 space-y-2 pl-6">
          <div className="flex flex-wrap gap-4 text-xs text-muted">
            <span>Risk tier: <span className="text-gray-200">{row.risk_tier}</span></span>
            {row.run_as_role && <span>Run-as: <span className="text-gray-200">{row.run_as_role}</span></span>}
            {detail && (
              <>
                <span className={`badge ${triStateClass(detail.connectable)}`}>connectable: {triState(detail.connectable)}</span>
                <span className={`badge ${triStateClass(detail.functional)}`}>functional: {triState(detail.functional)}</span>
              </>
            )}
          </div>
          <div className="overflow-x-auto rounded-lg border border-edge">
            <table className="w-full">
              <thead className="bg-ink">
                <tr>
                  <th className="th">Stage</th>
                  <th className="th">Outcome</th>
                  <th className="th">Bucket</th>
                  <th className="th">Message</th>
                  <th className="th">Duration</th>
                </tr>
              </thead>
              <tbody>
                {detail?.stage_outcomes?.map((s) => (
                  <tr key={s.id} className="border-t border-edge/50">
                    <td className="td font-mono text-xs text-muted">{s.stage}</td>
                    <td className="td"><Badge value={s.outcome} /></td>
                    <td className="td font-mono text-xs text-fail">{s.failure_bucket ?? '-'}</td>
                    <td className="td text-xs text-gray-300">{s.message}</td>
                    <td className="td text-xs text-muted">{duration(s.duration_ms)}</td>
                  </tr>
                ))}
                {detail && !detail.stage_outcomes.length && (
                  <tr><td className="td text-xs text-muted" colSpan={5}>No stage outcomes recorded (blocked before dispatch).</td></tr>
                )}
                {!detail && (
                  <tr><td className="td text-xs text-muted" colSpan={5}>Loading stage outcomes...</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function MetaGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="md:pl-8 md:first:pl-0">
      <div className="card-title mb-2">{title}</div>
      <dl className="space-y-1.5">{children}</dl>
    </div>
  )
}

function Meta({ label, value, title, mono }: { label: string; value: string; title?: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-sm">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className={`truncate text-right text-white ${mono ? 'font-mono text-xs' : ''}`} title={title ?? value}>
        {value}
      </dd>
    </div>
  )
}

function MetaBadge({ label, value, cls }: { label: string; value: string; cls: string }) {
  return (
    <div className="flex items-center justify-between gap-4 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd><span className={`badge ${cls}`}>{value}</span></dd>
    </div>
  )
}
