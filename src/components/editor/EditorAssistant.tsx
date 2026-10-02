import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle, Brain, Check, ChevronRight, FileDiff, Info, Loader2, RotateCcw, Send, Sparkles, Square, X,
} from 'lucide-react'

import { streamEditorTurn, useAssistantConfig, useBranding } from '../../hooks/queries'
import { errorMessage } from '../../lib/api'
import type {
  DryRunResponse, EditorEvent, EditorProposal, EditorStep, EditorTurnResponse, EditorWorkItem,
} from '../../types'
import { runSummary, tierClass } from './ResultsPanel'

export interface ChatEntry {
  role: 'user' | 'assistant'
  content: string
  steps?: EditorStep[]
  /** The turn's working log: interim notes, reasoning and tool calls, in order. */
  work?: EditorWorkItem[]
  /** How long the turn took. */
  ms?: number
  proposal?: EditorProposal | null
  dryRun?: DryRunResponse | null
  error?: boolean
}

const MAX_KEPT = 60
const MAX_KEPT_TEXT = 2000

function seconds(ms: number) {
  return ms < 10_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms / 1000)}s`
}

const isRunning = (w: EditorWorkItem, tool: string) => w.kind === 'tool' && w.running && w.tool === tool

/** Folds one stream event into the working log (returns a new array). */
function applyEvent(work: EditorWorkItem[], ev: EditorEvent): EditorWorkItem[] {
  switch (ev.type) {
    case 'message':
    case 'reasoning':
    case 'notice':
      return [...work, { kind: ev.type, text: ev.text }]
    case 'tool_start':
      return [...work, { kind: 'tool', running: true, tool: ev.tool, detail: ev.detail, ok: true }]
    case 'tool_end': {
      let i = work.length - 1
      while (i >= 0 && !isRunning(work[i], ev.tool)) i--
      const done: EditorWorkItem = {
        kind: 'tool', running: false, tool: ev.tool, detail: ev.detail, ok: ev.ok, result: ev.result, ms: ev.ms,
      }
      return i < 0 ? [...work, done] : [...work.slice(0, i), done, ...work.slice(i + 1)]
    }
    default:
      return work
  }
}

export function chatKey(testId: string | null) {
  return `editor.chat.${testId ?? 'new'}`
}

export function loadChat(testId: string | null): ChatEntry[] {
  try {
    const raw = localStorage.getItem(chatKey(testId))
    return raw ? (JSON.parse(raw) as ChatEntry[]) : []
  } catch {
    return []
  }
}

export function saveChat(testId: string | null, entries: ChatEntry[]) {
  try {
    const slim = entries.slice(-MAX_KEPT).map((e) => ({
      ...e,
      dryRun: undefined,
      work: e.work?.map((w) => (w.kind === 'tool' ? w : { ...w, text: w.text.slice(0, MAX_KEPT_TEXT) })),
    }))
    localStorage.setItem(chatKey(testId), JSON.stringify(slim))
  } catch {
    // Storage full or unavailable: the conversation still lives in memory.
  }
}

interface Props {
  testId: string | null
  entries: ChatEntry[]
  setEntries: (fn: (prev: ChatEntry[]) => ChatEntry[]) => void
  buffer: string
  targetId: string | null
  lastDryRun: DryRunResponse | null
  reviewing: boolean
  onProposal: (p: EditorProposal) => void
  onDryRun: (d: DryRunResponse) => void
  /** A request to send as soon as the editor is ready (e.g. handed off from the shelf). */
  initialAsk?: string | null
  onInitialAskSent?: () => void
}

export default function EditorAssistant({
  testId, entries, setEntries, buffer, targetId, lastDryRun, reviewing, onProposal, onDryRun,
  initialAsk, onInitialAskSent,
}: Props) {
  const { data: branding } = useBranding()
  const { data: config } = useAssistantConfig()
  const name = branding?.assistant_name || 'Assistant'
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [live, setLive] = useState<EditorWorkItem[]>([])
  const [startedAt, setStartedAt] = useState(0)
  const now = useTicker(busy)
  const abort = useRef<AbortController | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [entries.length, busy, live.length])

  useEffect(() => () => abort.current?.abort(), [])

  async function send(message: string) {
    const content = message.trim()
    if (!content || busy) return
    const history = [...entries, { role: 'user' as const, content }]
    setEntries(() => history)
    setText('')
    const controller = new AbortController()
    abort.current = controller
    const started = Date.now()
    setStartedAt(started)
    setLive([])
    setBusy(true)
    // Assigned from the stream callback, so declared without narrowing to null.
    let work: EditorWorkItem[] = []
    let proposal = null as EditorProposal | null
    let res = null as EditorTurnResponse | null
    try {
      await streamEditorTurn(
        {
          messages: history.map(({ role, content }) => ({ role, content })),
          script: buffer,
          test_id: testId,
          target_id: targetId,
          last_dry_run: lastDryRun,
        },
        (ev) => {
          if (ev.type === 'done') res = ev
          else if (ev.type === 'proposal') proposal = ev.proposal
          work = applyEvent(work, ev)
          setLive(work)
        },
        controller.signal,
      )
    } catch (e) {
      if (!controller.signal.aborted) {
        setEntries((prev) => [...prev, { role: 'assistant', content: errorMessage(e), work, error: true }])
        return
      }
    } finally {
      setBusy(false)
      setLive([])
      abort.current = null
    }
    const final: EditorTurnResponse = res ?? {
      reply: proposal ? 'Stopped. My latest edit is ready to review.' : 'Stopped.',
      proposal,
      steps: [],
      stopped: true,
    }
    setEntries((prev) => [
      ...prev,
      {
        role: 'assistant', content: final.reply, steps: final.steps, work: work.map((w) => (w.kind === 'tool' ? { ...w, running: false } : w)),
        ms: Date.now() - started, proposal: final.proposal, dryRun: final.dry_run, error: final.error,
      },
    ])
    if (final.dry_run) onDryRun(final.dry_run)
    if (final.proposal && final.proposal.script.trim() !== buffer.trim()) onProposal(final.proposal)
  }

  const sentInitial = useRef(false)
  useEffect(() => {
    if (!initialAsk || sentInitial.current) return
    sentInitial.current = true
    send(initialAsk)
    onInitialAskSent?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialAsk])

  const summary = runSummary(lastDryRun)
  const suggestions = !testId ? [
    'Write a test that every SQL warehouse on the target has auto-stop enabled.',
    "Write a test that analysts can query the sales catalog but can't read the payroll schema.",
    'Write a test that the workspace can reach our private PyPI mirror but not the public internet.',
    'Write a test that every cluster policy limits max workers to 20 or fewer.',
  ] : [
    ...(summary?.bad ? ['Why did the last dry run fail? Fix the test if the test is wrong.'] : []),
    'Explain what this test checks, check by check.',
    'Add a counterfactual: an identity without access should be denied.',
    'Make the assertions more specific and record() useful evidence.',
    'Look at the real data on the target and tighten the checks to match.',
  ]

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-edge px-3 py-2">
        <Sparkles size="15" className="text-accent" />
        <span className="text-sm font-semibold text-white">{name}</span>
        {config?.model ? (
          <Link
            to="/settings/assistant"
            className="min-w-0 truncate font-mono text-[11px] text-muted hover:text-white"
            title="The model the assistant uses. Change it in Settings → Assistant."
          >
            {config.model}
            {config.reasoning !== 'off' && ` · ${config.reasoning} reasoning`}
          </Link>
        ) : (
          <span className="text-[11px] text-muted">edits this test with you</span>
        )}
        {entries.length > 0 && (
          <button
            className="ml-auto flex shrink-0 items-center gap-1 text-[11px] text-muted hover:text-white"
            onClick={() => setEntries(() => [])}
            disabled={busy}
            title="Start a new conversation"
          >
            <RotateCcw size="12" /> New chat
          </button>
        )}
      </div>

      <div ref={scroller} className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
        {!entries.length && !busy && (
          <div className="space-y-3">
            <p className="text-xs text-muted">
              {!testId && 'Describe the test you want and I\'ll write it. '}
              Ask for a change and I'll propose an edit you can review as a diff. I look up SDK methods and
              REST docs before using them, can query the target with SQL or read-only API calls to see real
              data, and dry-run my proposals on the selected target. I never save or run the test for real.
            </p>
            <div className="space-y-1.5">
              {suggestions.map((s) => (
                <button
                  key={s}
                  className="w-full rounded-lg border border-edge bg-ink p-2 text-left text-xs text-gray-200 hover:border-accent hover:text-white"
                  onClick={() => {
                    setText(s)
                    input.current?.focus()
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {entries.map((e, i) =>
          e.role === 'user' ? (
            <div key={i} className="ml-8 whitespace-pre-wrap rounded-lg bg-ink-soft px-3 py-2 text-sm text-white">
              {e.content}
            </div>
          ) : (
            <div key={i} className="space-y-2">
              {(e.work?.length || e.steps?.length) ? (
                <WorkLog
                  work={e.work ?? e.steps!.map((s) => ({ kind: 'tool' as const, running: false, ...s }))}
                  ms={e.ms}
                />
              ) : null}
              {e.content && (
                <div className={`whitespace-pre-wrap text-sm ${e.error ? 'text-fail' : 'text-gray-200'}`}>
                  {e.error && <AlertCircle size="13" className="mr-1 inline" />}
                  {e.content}
                </div>
              )}
              {e.proposal && (
                <ProposalCard
                  proposal={e.proposal}
                  current={e.proposal.script.trim() === buffer.trim()}
                  reviewing={reviewing}
                  onReview={() => onProposal(e.proposal!)}
                />
              )}
            </div>
          ),
        )}

        {busy && (
          <div className="space-y-2">
            {live.length > 0 && <WorkLog work={live} live />}
            <div className="flex items-center gap-2 text-xs text-muted">
              <Loader2 size="13" className="shrink-0 animate-spin" />
              <span className="min-w-0 truncate">
                {live.some((w) => w.kind === 'tool' && w.running) ? 'Working…' : 'Thinking…'}
              </span>
              <span className="ml-auto shrink-0 tabular-nums">{seconds(Math.max(0, now - startedAt))}</span>
            </div>
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-edge p-2">
        <div className="flex items-end gap-2">
          <textarea
            ref={input}
            className="input max-h-48 min-h-[60px] flex-1 resize-y text-sm"
            placeholder={targetId ? 'Ask for a change, or ask about the target…' : 'Ask for a change (pick a target to let me dry-run it)…'}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send(text)
              }
            }}
          />
          {busy ? (
            <button className="btn" onClick={() => abort.current?.abort()} title="Stop this turn" aria-label="Stop">
              <Square size="13" className="fill-current" />
            </button>
          ) : (
            <button className="btn btn-accent" onClick={() => send(text)} disabled={!text.trim()} aria-label="Send">
              <Send size="15" />
            </button>
          )}
        </div>
        <div className="mt-1 px-1 text-[10px] text-muted">Enter to send · Shift+Enter for a new line</div>
      </div>
    </div>
  )
}

/** The current time, re-read every half second while `on`. */
function useTicker(on: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!on) return
    setNow(Date.now())
    const id = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(id)
  }, [on])
  return now
}

/** A turn's working log. Live while the turn runs; a collapsed summary after. */
function WorkLog({ work, ms, live = false }: { work: EditorWorkItem[]; ms?: number; live?: boolean }) {
  const [open, setOpen] = useState(false)
  const tools = work.filter((w): w is Extract<EditorWorkItem, { kind: 'tool' }> => w.kind === 'tool')
  const failed = tools.filter((s) => !s.ok && !s.running).length
  const items = (
    <ul className="space-y-1 px-2 py-1.5">
      {work.map((w, i) => (
        <WorkRow key={i} item={w} />
      ))}
    </ul>
  )
  if (live) return <div className="rounded-lg border border-edge bg-ink">{items}</div>
  return (
    <div className="rounded-lg border border-edge bg-ink">
      <button
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-[11px] text-muted hover:text-white"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <ChevronRight size="12" className={`shrink-0 transition ${open ? 'rotate-90' : ''}`} />
        <span className="shrink-0">
          {tools.length} {tools.length === 1 ? 'step' : 'steps'}
          {ms != null && ` · ${seconds(ms)}`}
        </span>
        <span className="truncate font-mono text-gray-400">{[...new Set(tools.map((s) => s.tool))].join(' · ')}</span>
        {failed > 0 && <span className="ml-auto shrink-0 text-warn">{failed} failed</span>}
      </button>
      {open && <div className="border-t border-edge">{items}</div>}
    </div>
  )
}

function WorkRow({ item }: { item: EditorWorkItem }) {
  if (item.kind === 'message') {
    return <li className="whitespace-pre-wrap text-[11px] text-gray-300">{item.text}</li>
  }
  if (item.kind === 'notice') {
    return (
      <li className="flex items-start gap-1.5 text-[11px] text-warn">
        <Info size="11" className="mt-0.5 shrink-0" />
        <span>{item.text}</span>
      </li>
    )
  }
  if (item.kind === 'reasoning') {
    return (
      <li className="text-[11px]">
        <details>
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-muted hover:text-white">
            <Brain size="11" className="shrink-0" /> Reasoning
          </summary>
          <div className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap border-l border-edge pl-2 text-gray-400">
            {item.text}
          </div>
        </details>
      </li>
    )
  }
  return (
    <li className="flex items-start gap-1.5 text-[11px]" title={item.tool}>
      {item.running ? (
        <Loader2 size="11" className="mt-0.5 shrink-0 animate-spin text-info" />
      ) : item.ok ? (
        <Check size="11" className="mt-0.5 shrink-0 text-pass" />
      ) : (
        <X size="11" className="mt-0.5 shrink-0 text-warn" />
      )}
      <span className="min-w-0 flex-1 break-words text-gray-200">
        {item.detail || item.tool}
        {item.result && <span className={item.ok ? 'text-muted' : 'text-warn'}> — {item.result}</span>}
      </span>
      {item.ms != null && <span className="shrink-0 tabular-nums text-muted">{seconds(item.ms)}</span>}
    </li>
  )
}

function ProposalCard({
  proposal, current, reviewing, onReview,
}: {
  proposal: EditorProposal
  current: boolean
  reviewing: boolean
  onReview: () => void
}) {
  const a = proposal.analysis
  return (
    <div className="rounded-lg border border-accent/40 bg-accent/5 p-2">
      <div className="flex items-center gap-2 text-xs">
        <FileDiff size="13" className="text-accent" />
        <span className="font-medium text-white">Proposed edit</span>
        <span className="text-muted">{a.checks.length} checks</span>
        <span className={tierClass(a.max_risk_tier)}>max {a.max_risk_tier}</span>
        {!a.ok && <span className="text-fail">{a.errors.length} errors</span>}
      </div>
      {proposal.summary && <div className="mt-1 text-xs text-gray-300">{proposal.summary}</div>}
      <div className="mt-2">
        {current ? (
          <span className="text-[11px] text-pass">
            <Check size="11" className="mr-1 inline" />
            In the editor
          </span>
        ) : (
          <button className="btn px-2 py-1 text-xs" onClick={onReview} disabled={reviewing}>
            <FileDiff size="12" /> Review diff
          </button>
        )}
      </div>
    </div>
  )
}
