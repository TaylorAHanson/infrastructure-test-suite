import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ExternalLink } from 'lucide-react'

import { Badge } from '../components/Badge'
import { PageHeader } from '../components/PageHeader'
import { useRun } from '../hooks/queries'
import { duration, timeAgo } from '../lib/format'
import type { RunStep } from '../types'

export default function RunDetail() {
  const { runId } = useParams()
  const { data: run, isLoading } = useRun(runId)

  if (isLoading || !run) return <div className="text-muted">Loading run...</div>

  return (
    <div>
      <Link to="/runs" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-white">
        <ArrowLeft size="14" /> Back to runs
      </Link>
      <PageHeader
        title={run.test_name ?? run.test_definition_id}
        subtitle={`Run ${run.run_id}`}
        actions={<><Badge value={run.status} /><Badge value={run.connectivity_outcome} /></>}
      />

      <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
        <Meta label="Mode" value={run.execution_mode} />
        <Meta label="Catalog" value={run.catalog ?? '-'} />
        <Meta label="Workspace" value={run.workspace_name ?? '-'} />
        <Meta label="Cloud / region" value={`${run.cloud} / ${run.region ?? '-'}`} />
        <Meta label="Requested by" value={run.requested_by} />
        <Meta label="Source" value={run.request_source} />
        <Meta label="Duration" value={duration(run.duration_ms)} />
        <Meta label="Finished" value={timeAgo(run.finished_at)} />
        <Meta label="App version" value={run.app_version} />
        <Meta label="Git SHA" value={run.git_sha} />
        <Meta label="Executor" value={run.executor_key ?? '-'} />
        <Meta label="Root cause" value={run.root_cause_bucket ?? '-'} />
      </div>

      {run.recommendation && (
        <div className="card mb-4 border-warn/40 bg-warn/5">
          <div className="card-title text-warn">Recommendation</div>
          <p className="mt-1 text-sm text-gray-200">{run.recommendation}</p>
        </div>
      )}

      <div className="mb-4 flex flex-wrap gap-2">
        {run.databricks_job_run_url && (
          <a className="btn" href={run.databricks_job_run_url} target="_blank" rel="noreferrer">
            <ExternalLink size="14" /> Databricks job run
          </a>
        )}
        {run.telemetry_logs_url && (
          <a className="btn" href={run.telemetry_logs_url} target="_blank" rel="noreferrer">
            <ExternalLink size="14" /> App logs
          </a>
        )}
        {run.telemetry_trace_id && (
          <span className="btn">Trace: {run.telemetry_trace_id}</span>
        )}
      </div>

      <div className="card-title mb-2">Probe steps</div>
      <div className="space-y-2">
        {run.steps?.map((s) => <StepCard key={s.id} step={s} />)}
      </div>
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

function StepCard({ step }: { step: RunStep }) {
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="w-24 font-mono text-xs text-muted">{step.step_kind}</span>
          <Badge value={step.outcome} />
          {step.connectivity !== 'UNKNOWN' && <Badge value={step.connectivity} />}
          <span className="text-sm text-gray-200">{step.message}</span>
        </div>
        <span className="text-xs text-muted">{duration(step.duration_ms)}</span>
      </div>
      {(step.error_class || step.root_cause_bucket) && (
        <div className="mt-2 text-xs text-fail">
          {step.error_class} {step.root_cause_bucket ? `· ${step.root_cause_bucket}` : ''}
        </div>
      )}
      {step.recommendation && (
        <div className="mt-1 text-xs text-warn">{step.recommendation}</div>
      )}
      {step.raw_payload_excerpt && (
        <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-ink p-3 font-mono text-[11px] text-gray-300">
          {step.raw_payload_excerpt}
        </pre>
      )}
      {step.evidence && Object.keys(step.evidence).length > 0 && (
        <pre className="mt-2 overflow-auto rounded-lg bg-ink p-3 font-mono text-[11px] text-info">
          {JSON.stringify(step.evidence, null, 2)}
        </pre>
      )}
    </div>
  )
}
