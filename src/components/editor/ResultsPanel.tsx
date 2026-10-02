import { useState } from 'react'
import {
  AlertCircle, AlertTriangle, Ban, CheckCircle2, ChevronDown, ChevronRight, CircleDashed,
  CircleSlash, Loader2, ShieldAlert, XCircle,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type {
  DryRunResponse, ScriptAnalysis, ScriptCallRecord, ScriptCheckOutcome, ScriptCheckResult,
} from '../../types'

export type PanelTab = 'problems' | 'checks' | 'run' | 'output'

const OUTCOME: Record<ScriptCheckOutcome, { icon: LucideIcon; cls: string; label: string }> = {
  pass: { icon: CheckCircle2, cls: 'text-pass', label: 'pass' },
  fail: { icon: XCircle, cls: 'text-fail', label: 'fail' },
  error: { icon: AlertCircle, cls: 'text-fail', label: 'error' },
  blocked: { icon: ShieldAlert, cls: 'text-warn', label: 'blocked' },
  skipped: { icon: CircleSlash, cls: 'text-skip', label: 'skipped' },
  not_reached: { icon: CircleDashed, cls: 'text-skip', label: 'not reached' },
}

export const tierClass = (tier?: string | null) =>
  tier === 'R0' ? 'text-pass' : tier === 'R1' ? 'text-warn' : tier ? 'text-fail' : 'text-muted'

export function runSummary(dry: DryRunResponse | null): { pass: number; bad: number; other: number } | null {
  if (!dry?.result) return null
  const c = dry.result.checks
  return {
    pass: c.filter((x) => x.outcome === 'pass').length,
    bad: c.filter((x) => x.outcome === 'fail' || x.outcome === 'error').length,
    other: c.filter((x) => x.outcome !== 'pass' && x.outcome !== 'fail' && x.outcome !== 'error').length,
  }
}

interface Props {
  tab: PanelTab
  setTab: (t: PanelTab) => void
  analysis: ScriptAnalysis | null
  dryRun: (DryRunResponse & { by: 'you' | 'assistant'; stale: boolean }) | null
  running: boolean
  runError: string | null
  onGoTo: (line: number) => void
  collapsed: boolean
  setCollapsed: (v: boolean) => void
}

export default function ResultsPanel({
  tab, setTab, analysis, dryRun, running, runError, onGoTo, collapsed, setCollapsed,
}: Props) {
  const problems = (analysis?.errors.length ?? 0) + (analysis?.warnings.length ?? 0)
  const summary = runSummary(dryRun)
  const output = dryRun?.result ? [dryRun.result.output, ...(dryRun.result.logs ?? [])].filter(Boolean) : []

  const tabs: { id: PanelTab; label: string; badge?: React.ReactNode }[] = [
    {
      id: 'problems',
      label: 'Problems',
      badge: problems ? (
        <span className={analysis?.errors.length ? 'text-fail' : 'text-warn'}>{problems}</span>
      ) : null,
    },
    { id: 'checks', label: 'Checks', badge: analysis ? <span className="text-muted">{analysis.checks.length}</span> : null },
    {
      id: 'run',
      label: 'Dry run',
      badge: running ? (
        <Loader2 size="11" className="animate-spin text-info" />
      ) : summary ? (
        <span className={summary.bad ? 'text-fail' : 'text-pass'}>
          {summary.pass}/{dryRun!.result!.checks.length}
        </span>
      ) : null,
    },
    { id: 'output', label: 'Output', badge: output.length ? <span className="text-muted">•</span> : null },
  ]

  return (
    <div className={`flex min-h-0 flex-col border-t border-edge bg-panel ${collapsed ? '' : 'h-[40%]'}`}>
      <div className="flex shrink-0 items-center gap-1 px-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => {
              setTab(t.id)
              setCollapsed(false)
            }}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-2.5 py-1.5 text-xs font-medium transition ${
              tab === t.id && !collapsed ? 'border-accent text-white' : 'border-transparent text-muted hover:text-white'
            }`}
          >
            {t.label}
            {t.badge}
          </button>
        ))}
        <button
          className="ml-auto p-1 text-muted hover:text-white"
          onClick={() => setCollapsed(!collapsed)}
          title={collapsed ? 'Show panel' : 'Hide panel'}
        >
          {collapsed ? <ChevronRight size="14" className="-rotate-90" /> : <ChevronDown size="14" />}
        </button>
      </div>
      {!collapsed && (
        <div className="min-h-0 flex-1 overflow-auto border-t border-edge/60 px-3 py-2 text-sm">
          {tab === 'problems' && <Problems analysis={analysis} onGoTo={onGoTo} />}
          {tab === 'checks' && <Outline analysis={analysis} onGoTo={onGoTo} />}
          {tab === 'run' && <DryRunView dryRun={dryRun} running={running} error={runError} onGoTo={onGoTo} />}
          {tab === 'output' &&
            (output.length ? (
              <pre className="whitespace-pre-wrap font-mono text-[12px] text-gray-200">{output.join('\n')}</pre>
            ) : (
              <Empty>print() and log() output from the last dry run shows here.</Empty>
            ))}
        </div>
      )}
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="py-2 text-xs text-muted">{children}</div>
}

function LineLink({ line, onGoTo }: { line?: number | null; onGoTo: (l: number) => void }) {
  if (!line) return null
  return (
    <button className="shrink-0 font-mono text-[11px] text-muted hover:text-accent" onClick={() => onGoTo(line)}>
      line {line}
    </button>
  )
}

function Problems({ analysis, onGoTo }: { analysis: ScriptAnalysis | null; onGoTo: (l: number) => void }) {
  if (!analysis) return <Empty>Checking…</Empty>
  const rows = [
    ...analysis.errors.map((d) => ({ ...d, sev: 'error' as const })),
    ...analysis.warnings.map((d) => ({ ...d, sev: 'warning' as const })),
  ].sort((a, b) => a.line - b.line)
  if (!rows.length) return <Empty>No problems. Every SDK call resolves and nothing is blocklisted.</Empty>
  return (
    <ul className="space-y-1">
      {rows.map((d, i) => (
        <li key={i} className="flex items-start gap-2">
          {d.sev === 'error' ? (
            <AlertCircle size="14" className="mt-0.5 shrink-0 text-fail" />
          ) : (
            <AlertTriangle size="14" className="mt-0.5 shrink-0 text-warn" />
          )}
          <span className="flex-1 text-gray-200">{d.message}</span>
          <LineLink line={d.line} onGoTo={onGoTo} />
        </li>
      ))}
    </ul>
  )
}

function Outline({ analysis, onGoTo }: { analysis: ScriptAnalysis | null; onGoTo: (l: number) => void }) {
  if (!analysis) return <Empty>Checking…</Empty>
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 card-title">Checks — each is one result in a run</div>
        {analysis.checks.length ? (
          <ul className="space-y-1">
            {analysis.checks.map((c) => (
              <li key={`${c.name}-${c.line}`} className="flex items-center gap-2">
                <span className="truncate text-gray-200">{c.name}</span>
                {c.kind === 'counterfactual' && (
                  <span className="badge bg-info/15 text-info">expect deny{c.expected_stage ? ` @ ${c.expected_stage}` : ''}</span>
                )}
                {c.advisory && <span className="badge bg-flaky/15 text-flaky">advisory</span>}
                {c.role && <span className="badge bg-ink-soft text-accent">as {c.role}</span>}
                <span className="ml-auto" />
                <LineLink line={c.line} onGoTo={onGoTo} />
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No checks yet. Wrap each claim in <code className="text-info">with check("…"):</code></Empty>
        )}
      </div>
      <div>
        <div className="mb-1 flex items-center gap-2 card-title">
          Calls
          <span className={`normal-case tracking-normal ${tierClass(analysis.max_risk_tier)}`}>max {analysis.max_risk_tier}</span>
        </div>
        {analysis.calls.length ? (
          <table className="w-full text-xs">
            <tbody>
              {analysis.calls.map((c, i) => (
                <tr key={i} className="border-t border-edge/40">
                  <td className="py-1 pr-2 font-mono text-info">{c.op}</td>
                  <td className="py-1 pr-2 text-muted">{c.verb}</td>
                  <td className={`py-1 pr-2 ${tierClass(c.tier)}`}>{c.tier}</td>
                  <td className="py-1 pr-2">{c.blocklisted && <span className="badge bg-fail/15 text-fail">blocklisted</span>}</td>
                  <td className="py-1 text-right"><LineLink line={c.line} onGoTo={onGoTo} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No Databricks calls yet.</Empty>
        )}
        <p className="mt-2 text-[11px] text-muted">
          R0 reads run anywhere. R1 creates/updates and R2 deletes only run at full depth on targets whose
          safety class allows them.
        </p>
      </div>
    </div>
  )
}

function DryRunView({
  dryRun, running, error, onGoTo,
}: {
  dryRun: Props['dryRun']
  running: boolean
  error: string | null
  onGoTo: (l: number) => void
}) {
  if (running) {
    return (
      <div className="flex items-center gap-2 py-2 text-xs text-muted">
        <Loader2 size="14" className="animate-spin" /> Running against the target…
      </div>
    )
  }
  if (error) return <div className="py-2 text-sm text-fail">{error}</div>
  if (!dryRun) {
    return (
      <Empty>
        Pick a target and press Run (⌘↵) to execute the script once. Nothing is recorded as a run; quick depth
        only makes read calls.
      </Empty>
    )
  }
  const result = dryRun.result
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>
          {dryRun.by === 'assistant' ? 'Assistant ran' : 'Ran'} on <span className="text-white">{dryRun.target?.name ?? '—'}</span>
        </span>
        <span className="badge bg-ink-soft text-muted">{dryRun.depth}</span>
        {result && <span>{(result.duration_ms / 1000).toFixed(1)}s</span>}
        {dryRun.stale && <span className="badge bg-warn/15 text-warn">script changed since</span>}
      </div>
      {!result && (
        <div className="text-sm text-fail">The script has syntax errors, so it wasn't run. See Problems.</div>
      )}
      {result?.error && (
        <div className="rounded-lg border border-fail/40 bg-fail/5 p-2">
          <div className="flex items-center gap-2 text-sm text-fail">
            <AlertCircle size="14" />
            <span className="flex-1">
              {result.error.kind === 'timeout' ? 'Timed out: ' : result.error.kind === 'crash' ? 'Sandbox crashed: ' : 'Script error: '}
              {result.error.message}
            </span>
            <LineLink line={result.error.line} onGoTo={onGoTo} />
          </div>
          {result.error.traceback && <Collapsible label="traceback" text={result.error.traceback} />}
        </div>
      )}
      {result && (
        <ul className="divide-y divide-edge/40 rounded-lg border border-edge">
          {result.checks.map((c) => (
            <CheckRow key={c.name} check={c} onGoTo={onGoTo} />
          ))}
          {!result.checks.length && <li className="p-2 text-xs text-muted">The script declared no checks.</li>}
        </ul>
      )}
      {result && result.setup_calls.length > 0 && (
        <div>
          <div className="mb-1 card-title">Calls outside checks</div>
          <CallList calls={result.setup_calls} onGoTo={onGoTo} />
        </div>
      )}
    </div>
  )
}

function CheckRow({ check, onGoTo }: { check: ScriptCheckResult; onGoTo: (l: number) => void }) {
  const [open, setOpen] = useState(check.outcome === 'fail' || check.outcome === 'error')
  const o = OUTCOME[check.outcome] ?? OUTCOME.not_reached
  const Icon = o.icon
  const recorded = check.evidence ?? null
  return (
    <li className="p-2">
      <div className="flex items-start gap-2">
        <button className="mt-0.5 text-muted hover:text-white" onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size="13" /> : <ChevronRight size="13" />}
        </button>
        <Icon size="15" className={`mt-0.5 shrink-0 ${o.cls}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-gray-100">{check.name}</span>
            <span className={`text-[11px] ${o.cls}`}>{o.label}</span>
            {check.kind === 'counterfactual' && <span className="badge bg-info/15 text-info">expect deny</span>}
            {check.advisory && <span className="badge bg-flaky/15 text-flaky">advisory</span>}
            {check.bucket && <span className="font-mono text-[10px] text-muted">{check.bucket}</span>}
            {recorded && Object.keys(recorded).length > 0 && (
              <span className="text-[10px] text-muted">{Object.keys(recorded).length} recorded</span>
            )}
          </div>
          {check.message && <div className="mt-0.5 whitespace-pre-wrap text-xs text-gray-300">{check.message}</div>}
        </div>
        {check.ms != null && <span className="shrink-0 text-[11px] text-muted">{check.ms}ms</span>}
        <LineLink line={check.line} onGoTo={onGoTo} />
      </div>
      {open && (
        <div className="ml-7 mt-2 space-y-2">
          {check.calls.length > 0 ? <CallList calls={check.calls} onGoTo={onGoTo} /> : (
            <div className="text-xs text-muted">No calls were made in this check.</div>
          )}
          {recorded && Object.keys(recorded).length > 0 && (
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Recorded</div>
              <pre className="max-h-48 overflow-auto rounded bg-ink p-2 text-[11px] text-gray-200">
                {JSON.stringify(recorded, null, 2)}
              </pre>
            </div>
          )}
          {check.traceback && <Collapsible label="traceback" text={check.traceback} />}
        </div>
      )}
    </li>
  )
}

function CallList({ calls, onGoTo }: { calls: ScriptCallRecord[]; onGoTo: (l: number) => void }) {
  return (
    <table className="w-full text-xs">
      <tbody>
        {calls.map((c, i) => (
          <tr key={i} className="align-top">
            <td className="py-0.5 pr-2">
              {c.outcome === 'ok' ? (
                <CheckCircle2 size="12" className="mt-0.5 text-pass" />
              ) : c.outcome === 'blocked' ? (
                <Ban size="12" className="mt-0.5 text-warn" />
              ) : c.outcome === 'skipped' ? (
                <CircleSlash size="12" className="mt-0.5 text-skip" />
              ) : (
                <XCircle size="12" className="mt-0.5 text-fail" />
              )}
            </td>
            <td className="py-0.5 pr-2 font-mono text-info">
              {c.op}
              {c.role && <span className="ml-1 text-accent">as {c.role}</span>}
              {c.message && <div className="whitespace-pre-wrap font-sans text-gray-400">{c.message}</div>}
            </td>
            <td className={`py-0.5 pr-2 ${tierClass(c.tier)}`}>{c.tier ?? ''}</td>
            <td className="py-0.5 pr-2 text-muted">{c.status_code ?? ''}</td>
            <td className="py-0.5 pr-2 text-right text-muted">{c.ms != null ? `${c.ms}ms` : ''}</td>
            <td className="py-0.5 text-right"><LineLink line={c.line} onGoTo={onGoTo} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Collapsible({ label, text }: { label: string; text: string }) {
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-[11px] text-muted">{label}</summary>
      <pre className="mt-1 max-h-60 overflow-auto rounded bg-ink p-2 text-[11px] text-gray-300">{text}</pre>
    </details>
  )
}
