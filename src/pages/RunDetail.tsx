import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ChevronDown, ChevronRight } from 'lucide-react'

import { Badge } from '../components/Badge'
import { PageHeader } from '../components/PageHeader'
import { useCheckRun, useRunCheckRuns, useRunResource, useTests } from '../hooks/queries'
import { duration, statusClass, timeAgo, triState, triStateClass } from '../lib/format'
import type { CheckRunRow } from '../types'

export default function RunDetail() {
  const { runId } = useParams()
  const { data: run, isLoading } = useRunResource(runId)
  const { data: checkRuns } = useRunCheckRuns(runId)
  const { data: tests } = useTests()

  if (isLoading || !run) return <div className="text-muted">Loading run...</div>

  const testName = (id: string) =>
    id === '(ad-hoc)' ? 'Ad-hoc Checks' : tests?.find((t) => t.id === id)?.name ?? id
  const rollup = Object.entries(run.test_rollup ?? {})
  const gating = Object.entries(run.failure_summary?.gating ?? {})
  const advisory = Object.entries(run.failure_summary?.advisory ?? {})

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

      <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
        <Meta label="Verdict" value={run.verdict} />
        <Meta label="Phase" value={run.phase} />
        <Meta label="Depth" value={run.depth} />
        <Meta label="Source" value={run.request_source} />
        <Meta label="Requested by" value={run.requested_by} />
        <Meta label="Targets" value={run.target_ids.length ? `${run.target_ids.length} selected` : 'all'} />
        <MetaBadge label="Connectable" value={triState(run.connectable)} cls={triStateClass(run.connectable)} />
        <MetaBadge label="Functional" value={triState(run.functional)} cls={triStateClass(run.functional)} />
        <Meta label="Created" value={timeAgo(run.created_at)} />
        <Meta label="Git SHA" value={run.git_sha} />
        <Meta label="App version" value={run.app_version} />
        <Meta label="CheckRuns" value={String(run.run_ids.length)} />
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
          <div className="text-xs uppercase tracking-wide text-muted">Gating (blocks the pipeline)</div>
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
          <div className="text-xs uppercase tracking-wide text-muted">Advisory (signal only, never gates)</div>
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

      <div className="card-title mb-2">Check results</div>
      <div className="space-y-2">
        {checkRuns?.map((cr) => <CheckRunRowView key={cr.run_id} row={cr} />)}
        {checkRuns && !checkRuns.length && (
          <div className="card text-sm text-muted">
            No CheckRuns yet — the run is still {run.phase === 'queued' ? 'queued on a target lease' : 'running'}.
          </div>
        )}
      </div>
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
        className="flex w-full items-center justify-between text-left"
        onClick={() => setOpen((o) => !o)}
      >
        <div className="flex items-center gap-3">
          {open ? <ChevronDown size="14" /> : <ChevronRight size="14" />}
          <span className="text-sm text-white">{row.check_name}</span>
          <Badge value={row.verdict} />
          <span className={`badge ${statusClass(row.evaluator)}`}>
            {row.evaluator}{row.gating ? '' : ' · advisory'}
          </span>
          {row.expectation === 'DENY' && <span className="badge bg-info/15 text-info">counterfactual</span>}
          {row.bucket_at_stage && <span className="font-mono text-xs text-fail">{row.bucket_at_stage}</span>}
        </div>
        <div className="flex items-center gap-3 text-xs text-muted">
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
          <div className="overflow-hidden rounded-lg border border-edge">
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

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="card py-2">
      <div className="card-title">{label}</div>
      <div className="mt-0.5 truncate text-sm text-white">{value}</div>
    </div>
  )
}

function MetaBadge({ label, value, cls }: { label: string; value: string; cls: string }) {
  return (
    <div className="card py-2">
      <div className="card-title">{label}</div>
      <div className="mt-0.5"><span className={`badge ${cls}`}>{value}</span></div>
    </div>
  )
}
