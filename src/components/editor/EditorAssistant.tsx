import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle, Check, ChevronRight, FileDiff, Loader2, RotateCcw, Send, Sparkles, X,
} from 'lucide-react'

import { useBranding, useEditorTurn } from '../../hooks/queries'
import { errorMessage } from '../../lib/api'
import type { DryRunResponse, EditorProposal, EditorStep } from '../../types'
import { runSummary, tierClass } from './ResultsPanel'

export interface ChatEntry {
  role: 'user' | 'assistant'
  content: string
  steps?: EditorStep[]
  proposal?: EditorProposal | null
  dryRun?: DryRunResponse | null
  error?: boolean
}

const MAX_KEPT = 60

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
    const slim = entries.slice(-MAX_KEPT).map((e) => ({ ...e, dryRun: undefined }))
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
  const turn = useEditorTurn()
  const { data: branding } = useBranding()
  const name = branding?.assistant_name || 'Assistant'
  const [text, setText] = useState('')
  const scroller = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [entries.length, turn.isPending])

  async function send(message: string) {
    const content = message.trim()
    if (!content || turn.isPending) return
    const history = [...entries, { role: 'user' as const, content }]
    setEntries(() => history)
    setText('')
    try {
      const res = await turn.mutateAsync({
        messages: history.map(({ role, content }) => ({ role, content })),
        script: buffer,
        test_id: testId,
        target_id: targetId,
        last_dry_run: lastDryRun,
      })
      setEntries((prev) => [
        ...prev,
        { role: 'assistant', content: res.reply, steps: res.steps, proposal: res.proposal, dryRun: res.dry_run, error: res.error },
      ])
      if (res.dry_run) onDryRun(res.dry_run)
      if (res.proposal && res.proposal.script.trim() !== buffer.trim()) onProposal(res.proposal)
    } catch (e) {
      setEntries((prev) => [...prev, { role: 'assistant', content: errorMessage(e), error: true }])
    }
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
        <span className="text-[11px] text-muted">edits this test with you</span>
        {entries.length > 0 && (
          <button
            className="ml-auto flex items-center gap-1 text-[11px] text-muted hover:text-white"
            onClick={() => setEntries(() => [])}
            disabled={turn.isPending}
            title="Start a new conversation"
          >
            <RotateCcw size="12" /> New chat
          </button>
        )}
      </div>

      <div ref={scroller} className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
        {!entries.length && !turn.isPending && (
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
              {e.steps && e.steps.length > 0 && <Steps steps={e.steps} />}
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

        {turn.isPending && (
          <div className="flex items-center gap-2 text-xs text-muted">
            <Loader2 size="13" className="animate-spin" /> Working — looking things up, editing, dry-running…
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
          <button className="btn btn-accent" onClick={() => send(text)} disabled={turn.isPending || !text.trim()}>
            <Send size="15" />
          </button>
        </div>
        <div className="mt-1 px-1 text-[10px] text-muted">Enter to send · Shift+Enter for a new line</div>
      </div>
    </div>
  )
}

function Steps({ steps }: { steps: EditorStep[] }) {
  const [open, setOpen] = useState(false)
  const failed = steps.filter((s) => !s.ok).length
  return (
    <div className="rounded-lg border border-edge bg-ink">
      <button
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-[11px] text-muted hover:text-white"
        onClick={() => setOpen(!open)}
      >
        <ChevronRight size="12" className={`transition ${open ? 'rotate-90' : ''}`} />
        {steps.length} {steps.length === 1 ? 'step' : 'steps'}
        <span className="truncate font-mono text-gray-400">
          {[...new Set(steps.map((s) => s.tool))].join(' · ')}
        </span>
        {failed > 0 && <span className="ml-auto text-warn">{failed} failed</span>}
      </button>
      {open && (
        <ul className="space-y-0.5 border-t border-edge px-2 py-1.5">
          {steps.map((s, i) => (
            <li key={i} className="flex items-start gap-1.5 text-[11px]">
              {s.ok ? <Check size="11" className="mt-0.5 shrink-0 text-pass" /> : <X size="11" className="mt-0.5 shrink-0 text-warn" />}
              <span className="font-mono text-info">{s.tool}</span>
              <span className="min-w-0 break-words text-gray-400">{s.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
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
