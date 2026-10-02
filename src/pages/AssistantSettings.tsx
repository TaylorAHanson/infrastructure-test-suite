import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { CheckCircle2, Loader2, RefreshCw, XCircle } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'

import { PageHeader } from '../components/PageHeader'
import {
  useAssistantConfig, useAssistantModels, useTestAssistantModel, useUpdateAssistantConfig,
} from '../hooks/queries'
import { api, errorMessage } from '../lib/api'
import { timeAgo } from '../lib/format'
import type { AssistantConfig, AssistantModel, ReasoningLevel } from '../types'

const LEVELS: { id: ReasoningLevel; label: string }[] = [
  { id: 'off', label: 'Off' },
  { id: 'low', label: 'Low' },
  { id: 'medium', label: 'Medium' },
  { id: 'high', label: 'High' },
]

type Form = Pick<
  AssistantConfig,
  'reasoning' | 'max_tool_calls' | 'max_output_tokens' | 'temperature' | 'turn_timeout_seconds' | 'allow_live_reads' | 'allow_dry_run'
> & { endpoint: string }

function toForm(c: AssistantConfig): Form {
  return {
    endpoint: c.endpoint ?? '',
    reasoning: c.reasoning,
    max_tool_calls: c.max_tool_calls,
    max_output_tokens: c.max_output_tokens,
    temperature: c.temperature,
    turn_timeout_seconds: c.turn_timeout_seconds,
    allow_live_reads: c.allow_live_reads,
    allow_dry_run: c.allow_dry_run,
  }
}

// Which model the assistant uses and how far one turn may go. Edits are audited.
export default function AssistantSettings() {
  const { data: config } = useAssistantConfig()
  const update = useUpdateAssistantConfig()
  const [form, setForm] = useState<Form | null>(null)

  useEffect(() => {
    if (config) setForm(toForm(config))
  }, [config])

  if (!config || !form) {
    return (
      <div>
        <PageHeader title="Assistant" subtitle="The model behind the test-editor assistant, and its limits." />
        <div className="card text-sm text-muted">Loading…</div>
      </div>
    )
  }

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => (f ? { ...f, [key]: value } : f))
  const dirty = JSON.stringify(form) !== JSON.stringify(toForm(config))
  const effective = form.endpoint.trim() || config.default_endpoint || ''
  const range = (key: string) => config.limits[key] ?? [0, Number.MAX_SAFE_INTEGER]
  const thinking = effective.toLowerCase().includes('claude') && form.reasoning !== 'off'

  return (
    <div>
      <PageHeader title="Assistant" subtitle="The model behind the test-editor assistant, and its limits." />

      <div className="card mb-4">
        <h2 className="mb-1 text-sm font-semibold text-white">Model</h2>
        <p className="mb-3 text-xs text-muted">
          Any chat endpoint in this workspace. Foundation models are the pay-per-token endpoints serving{' '}
          <span className="font-mono">system.ai</span> models. Leave it empty to use the deployment default
          {config.default_endpoint ? (
            <>
              {' '}(<span className="font-mono">{config.default_endpoint}</span>)
            </>
          ) : (
            ' (none is set)'
          )}
          .
        </p>
        <ModelPicker value={form.endpoint} onChange={(v) => set('endpoint', v)} placeholder={config.default_endpoint ?? ''} />
        <ModelTest endpoint={effective} reasoning={form.reasoning} />

        <h3 className="mb-1 mt-5 text-xs font-semibold text-white">Reasoning</h3>
        <div className="mb-1 inline-flex overflow-hidden rounded-lg border border-edge" role="radiogroup" aria-label="Reasoning">
          {LEVELS.map((l) => (
            <button
              key={l.id}
              role="radio"
              aria-checked={form.reasoning === l.id}
              className={`px-3 py-1 text-xs ${form.reasoning === l.id ? 'bg-accent/20 text-white' : 'text-muted hover:text-white'}`}
              onClick={() => set('reasoning', l.id)}
            >
              {l.label}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted">
          Lets the model think before it answers: slower and more tokens, often better on hard edits. Claude gets a
          thinking budget; GPT, Gemini and the open models get a reasoning effort. If a model won't reason while
          calling tools, the assistant runs without it and says so. The reasoning shows in the assistant's work log.
        </p>
      </div>

      <div className="card mb-4">
        <h2 className="mb-3 text-sm font-semibold text-white">Limits for one turn</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField
            label="Tool calls"
            help="Lookups, edits and dry runs per request. At the limit it stops and reports what's left."
            value={form.max_tool_calls}
            range={range('max_tool_calls')}
            onChange={(v) => set('max_tool_calls', v)}
          />
          <NumberField
            label="Time limit (seconds)"
            help="Wall-clock time per request. Checked between steps."
            value={form.turn_timeout_seconds}
            range={range('turn_timeout_seconds')}
            onChange={(v) => set('turn_timeout_seconds', v)}
          />
          <NumberField
            label="Output tokens per reply"
            help="Room for one model reply. Writing a whole script needs a few thousand."
            value={form.max_output_tokens}
            range={range('max_output_tokens')}
            step={256}
            onChange={(v) => set('max_output_tokens', v)}
          />
          <NumberField
            label="Temperature"
            help={thinking ? "Ignored while Claude is thinking: it requires the model's default." : 'Lower is more predictable. 0.1 suits code. Models that refuse a temperature, like Claude Opus 5.5, run at their default.'}
            value={form.temperature}
            range={range('temperature')}
            step={0.1}
            onChange={(v) => set('temperature', v)}
          />
        </div>
      </div>

      <div className="card mb-4">
        <h2 className="mb-3 text-sm font-semibold text-white">What it may do on the selected target</h2>
        <label className="mb-2 flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={form.allow_live_reads} onChange={(e) => set('allow_live_reads', e.target.checked)} />
          <span>
            Read live data
            <span className="block text-xs text-muted">Read-only SDK calls and SQL, as the target's identity and through its safety gate.</span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={form.allow_dry_run} onChange={(e) => set('allow_dry_run', e.target.checked)} />
          <span>
            Dry-run its edits
            <span className="block text-xs text-muted">At quick depth: read calls run, mutating calls are skipped. Nothing is recorded.</span>
          </span>
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button className="btn btn-accent" disabled={!dirty || update.isPending} onClick={() => update.mutate(form)}>
          {update.isPending ? 'Saving…' : 'Save'}
        </button>
        {dirty && (
          <button className="btn" onClick={() => setForm(toForm(config))} disabled={update.isPending}>
            Reset
          </button>
        )}
        {update.isError && <span className="text-xs text-warn">{errorMessage(update.error)}</span>}
        {!dirty && !update.isError && (
          <span className="text-xs text-muted">
            Last changed by {config.updated_by} {timeAgo(config.updated_at)}
          </span>
        )}
      </div>
    </div>
  )
}

function NumberField({
  label, help, value, range, step = 1, onChange,
}: {
  label: string
  help: string
  value: number
  range: [number, number]
  step?: number
  onChange: (v: number) => void
}) {
  const id = useId()
  const [lo, hi] = range
  const bad = !Number.isFinite(value) || value < lo || value > hi
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs text-gray-300">{label}</label>
      <input
        id={id}
        type="number"
        className={`input w-40 ${bad ? 'border-warn' : ''}`}
        value={Number.isFinite(value) ? value : ''}
        min={lo}
        max={hi}
        step={step}
        onChange={(e) => onChange(e.target.valueAsNumber)}
      />
      <p className={`mt-1 text-xs ${bad ? 'text-warn' : 'text-muted'}`}>{bad ? `Between ${lo} and ${hi}.` : help}</p>
    </div>
  )
}

/** A typeahead over the workspace's chat endpoints; free text is allowed too. */
function ModelPicker({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  const { data, isFetching } = useAssistantModels()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()
  const box = useRef<HTMLDivElement>(null)
  const models = useMemo(() => data?.models ?? [], [data])
  const query = value.trim().toLowerCase()
  const matches = useMemo(
    () => models.filter((m) => !query || m.name.toLowerCase().includes(query) || (m.entity ?? '').toLowerCase().includes(query)).slice(0, 80),
    [models, query],
  )
  const known = models.find((m) => m.name === value.trim())

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])

  const pick = (m: AssistantModel) => {
    onChange(m.name)
    setOpen(false)
  }

  return (
    <div ref={box} className="relative max-w-xl">
      <div className="flex items-center gap-2">
        <input
          className="input w-full font-mono text-sm"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          placeholder={placeholder ? `${placeholder} (deployment default)` : 'Search models…'}
          value={value}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            onChange(e.target.value)
            setActive(0)
            setOpen(true)
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              setOpen(true)
              setActive((a) => Math.min(a + 1, matches.length - 1))
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              setActive((a) => Math.max(a - 1, 0))
            } else if (e.key === 'Enter' && open && matches[active]) {
              e.preventDefault()
              pick(matches[active])
            } else if (e.key === 'Escape') {
              setOpen(false)
            }
          }}
        />
        <button
          className="btn shrink-0 px-2"
          title="Refresh the model list"
          aria-label="Refresh the model list"
          onClick={() =>
            qc.fetchQuery({ queryKey: ['assistant-models'], queryFn: () => api.get('/assistant/models?refresh=true') })
          }
        >
          <RefreshCw size="14" className={isFetching ? 'animate-spin' : ''} />
        </button>
      </div>
      {known?.entity && <div className="mt-1 font-mono text-[11px] text-muted">serves {known.entity}</div>}
      {value.trim() && models.length > 0 && !known && (
        <div className="mt-1 text-[11px] text-warn">Not a chat endpoint in this workspace; use Test to check it answers.</div>
      )}
      {data?.error && <div className="mt-1 text-[11px] text-warn">{data.error}</div>}
      {open && matches.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-edge bg-panel py-1 shadow-xl"
        >
          {matches.map((m, i) => {
            const header = i === 0 || matches[i - 1].foundation !== m.foundation
            return (
              <li key={m.name} role="presentation">
                {header && (
                  <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted">
                    {m.foundation ? 'Foundation models (system.ai)' : 'Other chat endpoints'}
                  </div>
                )}
                <div
                  role="option"
                  aria-selected={i === active}
                  className={`flex cursor-pointer items-baseline gap-2 px-3 py-1 ${i === active ? 'bg-accent/15' : 'hover:bg-ink-soft'}`}
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    pick(m)
                  }}
                >
                  <span className="font-mono text-sm text-white">{m.name}</span>
                  {m.entity && m.entity !== `system.ai.${m.name}` && (
                    <span className="truncate font-mono text-[11px] text-muted">{m.entity}</span>
                  )}
                  {!m.ready && <span className="ml-auto shrink-0 text-[10px] text-warn">not ready</span>}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function ModelTest({ endpoint, reasoning }: { endpoint: string; reasoning: ReasoningLevel }) {
  const test = useTestAssistantModel()
  const result = test.data
  useEffect(() => test.reset(), [endpoint, reasoning]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      <button className="btn px-2 py-0.5 text-xs" disabled={!endpoint || test.isPending} onClick={() => test.mutate({ endpoint, reasoning })}>
        {test.isPending ? <Loader2 size="12" className="animate-spin" /> : null}
        Test
      </button>
      <span className="text-muted">Sends one small tool-calling request, the way the assistant does.</span>
      {test.isError && <span className="text-warn">{errorMessage(test.error)}</span>}
      {result && (
        <span className={`flex basis-full items-start gap-1 ${result.ok ? 'text-pass' : 'text-warn'}`}>
          {result.ok ? <CheckCircle2 size="13" className="mt-px shrink-0" /> : <XCircle size="13" className="mt-px shrink-0" />}
          {result.ok
            ? `Answered in ${(result.ms / 1000).toFixed(1)}s${result.called_tools ? ' and called the tool' : ", but didn't call the tool"}` +
              `${result.reasoning_shown ? ', with reasoning' : ''}.${result.notes?.length ? ` ${result.notes.join(' ')}` : ''}`
            : result.error}
        </span>
      )}
    </div>
  )
}
