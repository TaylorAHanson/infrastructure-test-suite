import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Sparkles, Save, Play } from 'lucide-react'

import { Badge } from '../components/Badge'
import { PageHeader } from '../components/PageHeader'
import { api } from '../lib/api'
import { duration } from '../lib/format'
import type { Run, RunStep } from '../types'
import {
  useAuthoringCapabilities,
  useCreateTest,
  useGenerateTest,
} from '../hooks/queries'

type Spec = Record<string, unknown>

const MODES = ['PREFLIGHT', 'SMOKE', 'FULL']
const CATEGORIES = ['PRODUCER', 'PROCESSING', 'CONSUMER']

export default function Authoring() {
  const { data: caps } = useAuthoringCapabilities()
  const generate = useGenerateTest()
  const createTest = useCreateTest()

  const [description, setDescription] = useState('')
  const [category, setCategory] = useState('')
  const [spec, setSpec] = useState<Spec | null>(null)
  const [paramsText, setParamsText] = useState('{}')
  const [error, setError] = useState<string | null>(null)
  const [savedId, setSavedId] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [runs, setRuns] = useState<Run[]>([])

  const execKeys = useMemo(() => caps?.executors.map((e) => e.executor_key) ?? [], [caps])
  const aiAvailable = caps?.ai_available ?? false

  function set<T = unknown>(key: string, value: T) {
    setSpec((s) => (s ? { ...s, [key]: value } : s))
    setSavedId(null)
  }

  async function onGenerate() {
    setError(null)
    setSavedId(null)
    if (!description.trim()) {
      setError('Describe the test you want first.')
      return
    }
    try {
      const res = await generate.mutateAsync({ description, category: category || undefined })
      setSpec(res.spec)
      setParamsText(JSON.stringify(res.spec.default_parameters ?? {}, null, 2))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Generation failed.')
    }
  }

  function onPickExecutor(key: string) {
    const meta = caps?.executors.find((e) => e.executor_key === key)
    setSpec((s) => (s ? { ...s, executor_key: key, executor_kind: meta?.kind ?? s.executor_kind } : s))
    setSavedId(null)
  }

  function toggleMode(mode: string) {
    if (!spec) return
    const modes = new Set((spec.supported_execution_modes as string[]) ?? [])
    if (modes.has(mode)) modes.delete(mode)
    else modes.add(mode)
    set('supported_execution_modes', MODES.filter((m) => modes.has(m)))
  }

  function buildPayload(): Spec | null {
    if (!spec) return null
    let params: unknown
    try {
      params = JSON.parse(paramsText || '{}')
    } catch {
      setError('Default parameters must be valid JSON.')
      return null
    }
    return { ...spec, default_parameters: params, required_capabilities: [spec.executor_key] }
  }

  async function onSave(): Promise<string | null> {
    setError(null)
    const payload = buildPayload()
    if (!payload) return null
    try {
      const created = await createTest.mutateAsync(payload)
      setSavedId(created.id)
      return created.id
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed.')
      return null
    }
  }

  async function onSaveAndRun() {
    setError(null)
    setRuns([])
    const id = savedId ?? (await onSave())
    if (!id) return
    const mode = (spec?.supported_execution_modes as string[])?.includes('SMOKE') ? 'SMOKE' : 'PREFLIGHT'
    setRunning(true)
    try {
      // inline=true executes synchronously and returns the run id(s) right away,
      // so we can show the result here instead of waiting on the poller. A single
      // definition fans out into one run per enabled target, so expect >=1.
      const req = await api.post<{ run_ids: string[] }>('/runs?inline=true', {
        test_definition_ids: [id],
        execution_mode: mode,
        requested_by: 'authoring-ui',
      })
      const ids = req.run_ids ?? []
      if (!ids.length) throw new Error('Run did not produce a result.')
      const details = await Promise.all(ids.map((rid) => api.get<Run>(`/runs/${rid}`)))
      setRuns(details)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Run failed.')
    } finally {
      setRunning(false)
    }
  }

  const meta = caps?.executors.find((e) => e.executor_key === spec?.executor_key)

  return (
    <div>
      <PageHeader
        title="Test Authoring"
        subtitle="Describe a check in plain English; AI drafts a runnable test you can review, save, and run"
      />

      <div className="card mb-4">
        <div className="mb-1 flex items-center gap-2">
          <Sparkles size="16" className="text-accent" />
          <span className="card-title">Describe the test</span>
          {caps && (
            <span className={`badge ml-auto ${aiAvailable ? 'text-pass' : 'text-warn'}`}>
              {aiAvailable ? `AI: ${caps.model_serving_endpoint}` : 'AI endpoint not configured'}
            </span>
          )}
        </div>
        <textarea
          className="input min-h-[90px] w-full"
          placeholder="e.g. Verify our serverless SQL warehouse can run SELECT 1; or check egress to https://api.stripe.com returns 200"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <div className="mt-3 flex items-center gap-3">
          <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Auto category</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <button className="btn btn-accent" onClick={onGenerate} disabled={generate.isPending || !aiAvailable}>
            <Sparkles size="16" />
            {generate.isPending ? 'Generating...' : 'Generate with AI'}
          </button>
          {!aiAvailable && caps && (
            <span className="text-sm text-muted">Set MODEL_SERVING_ENDPOINT to enable AI drafting.</span>
          )}
        </div>
        {error && <div className="mt-2 text-sm text-fail">{error}</div>}
      </div>

      {spec && (
        <div className="card mb-4">
          <div className="card-title mb-3">Review &amp; edit the draft</div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <label className="flex flex-col gap-1">
              <span className="card-title">Display name</span>
              <input className="input" value={String(spec.display_name ?? '')}
                     onChange={(e) => set('display_name', e.target.value)} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="card-title">Category</span>
              <select className="input" value={String(spec.category ?? '')} onChange={(e) => set('category', e.target.value)}>
                {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 md:col-span-2">
              <span className="card-title">Description</span>
              <input className="input" value={String(spec.description ?? '')}
                     onChange={(e) => set('description', e.target.value)} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="card-title">Executor</span>
              <select className="input" value={String(spec.executor_key ?? '')} onChange={(e) => onPickExecutor(e.target.value)}>
                {execKeys.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
              {meta && <span className="text-[11px] text-muted">{meta.purpose}</span>}
            </label>
            <div className="flex flex-col gap-1">
              <span className="card-title">Execution modes</span>
              <div className="flex gap-3 pt-1">
                {MODES.map((m) => (
                  <label key={m} className="flex items-center gap-1 text-sm text-gray-200">
                    <input
                      type="checkbox"
                      checked={((spec.supported_execution_modes as string[]) ?? []).includes(m)}
                      onChange={() => toggleMode(m)}
                    />
                    {m}
                  </label>
                ))}
              </div>
            </div>
            <label className="flex flex-col gap-1 md:col-span-2">
              <span className="card-title">Default parameters (JSON)</span>
              <textarea className="input min-h-[120px] font-mono text-xs" value={paramsText}
                        onChange={(e) => setParamsText(e.target.value)} />
              {meta && (
                <span className="text-[11px] text-muted">
                  Params for {meta.executor_key}: {Object.entries(meta.params).map(([k, v]) => `${k} (${v})`).join('; ') || 'none'}
                </span>
              )}
            </label>
          </div>

          <div className="mt-4 flex items-center gap-3">
            <button className="btn" onClick={onSave} disabled={createTest.isPending || running}>
              <Save size="16" />
              {createTest.isPending ? 'Saving...' : savedId ? 'Saved ✓' : 'Save test'}
            </button>
            <button className="btn btn-accent" onClick={onSaveAndRun} disabled={running || createTest.isPending}>
              <Play size="16" />
              {running ? 'Running...' : 'Save & run now'}
            </button>
            {savedId && !running && (
              <span className="text-sm text-pass">Saved to catalog (see Test Catalog).</span>
            )}
          </div>
        </div>
      )}

      {running && (
        <div className="card text-sm text-muted">Executing probe steps...</div>
      )}

      {!running && runs.length > 0 && (
        <div className="space-y-3">
          <div className="card-title">
            Run result{runs.length > 1 ? `s (${runs.length} targets)` : ''}
          </div>
          {runs.map((run) => <RunResult key={run.run_id} run={run} />)}
        </div>
      )}
    </div>
  )
}

function RunResult({ run }: { run: Run }) {
  const scope = run.catalog || run.workspace_name || run.target_id || 'workspace'
  return (
    <div className="card">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Badge value={run.status} />
        <Badge value={run.connectivity_outcome} />
        <span className="text-sm text-gray-200">{run.test_name ?? run.test_definition_id}</span>
        <span className="badge bg-ink-soft text-muted">{scope}</span>
        <span className="text-xs text-muted">{duration(run.duration_ms)}</span>
        <Link to={`/runs/${run.run_id}`} className="ml-auto text-sm text-accent">
          Open full run →
        </Link>
      </div>

      {run.recommendation && (
        <div className="mb-3 rounded-lg border border-warn/40 bg-warn/5 p-3 text-sm text-gray-200">
          <span className="font-semibold text-warn">Recommendation: </span>
          {run.recommendation}
        </div>
      )}

      <div className="space-y-2">
        {run.steps?.map((s) => <StepRow key={s.id} step={s} />)}
        {!run.steps?.length && (
          <div className="text-sm text-muted">No steps recorded for this run.</div>
        )}
      </div>
    </div>
  )
}

function StepRow({ step }: { step: RunStep }) {
  return (
    <div className="rounded-lg border border-edge bg-ink p-3">
      <div className="flex items-center gap-3">
        <span className="w-24 shrink-0 font-mono text-xs text-muted">{step.step_kind}</span>
        <Badge value={step.outcome} />
        {step.connectivity !== 'UNKNOWN' && <Badge value={step.connectivity} />}
        <span className="text-sm text-gray-200">{step.message}</span>
        <span className="ml-auto shrink-0 text-xs text-muted">{duration(step.duration_ms)}</span>
      </div>
      {(step.error_class || step.root_cause_bucket) && (
        <div className="mt-2 text-xs text-fail">
          {step.error_class} {step.root_cause_bucket ? `· ${step.root_cause_bucket}` : ''}
        </div>
      )}
      {step.evidence && Object.keys(step.evidence).length > 0 && (
        <pre className="mt-2 overflow-auto rounded-lg bg-panel p-2 font-mono text-[11px] text-info">
          {JSON.stringify(step.evidence, null, 2)}
        </pre>
      )}
    </div>
  )
}
