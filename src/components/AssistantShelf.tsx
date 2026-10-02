import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Sparkles, X, Send, Lightbulb, Search, GripVertical,
  Check, ChevronRight, ArrowUpRight, Wand2, Code2,
} from 'lucide-react'

import { useAssistantContext } from '../lib/assistantContext'
import type { AssistantMode } from '../lib/assistantContext'
import { useAssistantTurn, useBranding, useStarterPrompts } from '../hooks/queries'
import { errorMessage } from '../lib/api'
import type { NextAction, RcaSuggestion, ToolCall, WorkedExample } from '../types'

interface AssistantShelfProps {
  open: boolean
  onOpen: () => void
  onClose: () => void
  // Live shelf width (px) and the drag-handle mousedown, owned by Layout via
  // useShelfWidth so the sidebar can collapse when the shelf gets wide (#58).
  width: number
  onResizeStart: (e: React.MouseEvent) => void
}

// The single, app-wide Assistant surface (ADR-0012): a resizable push-pane
// (issue #58) that shares horizontal space with <main> rather than covering it.
// It hands the harness the page's declared context and answers questions or
// suggests an RCA. Writing a test hands off to the test editor, where a
// dedicated assistant works on the script beside the code.
export default function AssistantShelf({ open, onOpen, onClose, width, onResizeStart }: AssistantShelfProps) {
  const { pageContext, seedPrompt, openWithPrompt } = useAssistantContext()
  const navigate = useNavigate()
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // Set when a seed request arrives while the input isn't mounted yet; the
  // focus effect below consumes it once the shelf has opened and rendered.
  const pendingFocus = useRef(false)
  // The capability a seeded conversation asked for (e.g. "Describe a new test" →
  // authoring mode). It sticks across follow-up turns until another entry point re-seeds
  // with a different mode, or the user moves to another page.
  const [mode, setMode] = useState<AssistantMode>('auto')
  const turn = useAssistantTurn()
  const { data: starters } = useStarterPrompts()
  // The Assistant's display name is a branding setting (ADR-0012/0013), default "Assistant".
  const { data: branding } = useBranding()
  const assistantName = branding?.assistant_name || 'Assistant'

  const [text, setText] = useState('')
  // Set when the turn was a request to write a test: the editor takes it from here.
  const [authoring, setAuthoring] = useState<string | null>(null)
  const [rca, setRca] = useState<RcaSuggestion | null>(null)
  const [answer, setAnswer] = useState<string | null>(null)
  // Tool-call transparency + dynamic follow-up buttons for the last answer (#65).
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([])
  const [nextActions, setNextActions] = useState<NextAction[]>([])
  const [error, setError] = useState<string | null>(null)

  // Declared before the seed effect so a seed that lands with a page change wins.
  useEffect(() => setMode('auto'), [pageContext.page])

  // A seed request from any entry point (e.g. the Tests page's "Describe a new test" button) opens
  // the shelf and pre-fills the input with an editable starting sentence — not
  // gated on the conversation being empty, so it works mid-session too. Prior
  // results are cleared so the seeded turn starts clean; we never auto-send, so
  // the human still decides when to ask.
  useEffect(() => {
    if (!seedPrompt) return
    onOpen()
    setText(seedPrompt.text)
    if (seedPrompt.mode) setMode(seedPrompt.mode)
    reset()
    pendingFocus.current = true
    // Re-run on every request (nonce changes even when text repeats).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedPrompt])

  // Focus + place the caret at the end once the shelf has opened and the input
  // is actually mounted (it only renders while `open`), so a seed opening a
  // closed shelf still lands the cursor in the pre-filled prompt.
  useEffect(() => {
    if (!open || !pendingFocus.current) return
    pendingFocus.current = false
    const el = inputRef.current
    if (el) {
      el.focus()
      const end = el.value.length
      el.setSelectionRange(end, end)
    }
  }, [open])

  function reset() {
    setAuthoring(null)
    setRca(null)
    setAnswer(null)
    setToolCalls([])
    setNextActions([])
    setError(null)
  }

  async function ask() {
    if (!text.trim()) return
    reset()
    try {
      const res = await turn.mutateAsync({ turn: text, page_context: pageContext, mode })
      if (res.mode === 'authoring') {
        setAuthoring(text)
        setAnswer(res.draft?.clarifying_question ?? null)
      } else if (res.mode === 'query') setAnswer(res.answer ?? null)
      else setRca(res.rca ?? null)
      // Additive: which tools ran + the contextual follow-up buttons (#65).
      setToolCalls(res.tool_calls ?? [])
      setNextActions(res.next_actions ?? [])
    } catch (e) {
      setError(errorMessage(e) || 'The Assistant could not respond.')
    }
  }

  function openEditor(intent: string) {
    navigate(`/tests/new?ask=${encodeURIComponent(intent)}`)
  }

  // Run a dynamic next-action button (#65): `author` re-seeds the input with a
  // starting sentence; `navigate` routes the main pane (the shelf stays open, so
  // both are visible — the push-pane layout, #58).
  function runNextAction(action: NextAction) {
    if (action.kind === 'author' && action.seed) openWithPrompt(action.seed)
    else if (action.kind === 'navigate' && action.href) navigate(action.href)
  }

  // Closed: a floating launcher, out of flow so it never takes split-pane width.
  if (!open) {
    return (
      <button className="btn btn-accent fixed bottom-6 right-6 z-40 shadow-lg" onClick={onOpen}>
        <Sparkles size="16" />
        {assistantName}
      </button>
    )
  }

  // Open: a split-pane flex sibling of <main> (not an overlay), width-driven by
  // Layout, with a drag handle pinned to its left edge.
  return (
    <aside
      className="relative flex h-full shrink-0 flex-col border-l border-edge bg-ink shadow-2xl"
      style={{ width }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize assistant"
        onMouseDown={onResizeStart}
        className="group absolute left-0 top-0 z-10 flex h-full w-1.5 -translate-x-1/2 cursor-col-resize items-center justify-center hover:bg-accent/30"
      >
        <GripVertical
          size="14"
          className="text-edge opacity-0 transition group-hover:opacity-100"
        />
      </div>

      <div className="flex items-center gap-2 border-b border-edge p-4">
        <Sparkles size="18" className="text-accent" />
        <span className="text-sm font-semibold text-white">{assistantName}</span>
        <span className="badge bg-panel text-muted">{pageContext.page || 'app'}</span>
        <button className="ml-auto text-muted hover:text-white" onClick={onClose}>
          <X size="18" />
        </button>
      </div>

          <div className="flex-1 overflow-auto p-4">
            <p className="mb-3 text-xs text-muted">
              Ask about your tests, targets and runs, or why a check regressed. To write
              a test, describe it and we'll open it in the test editor.
            </p>

            {error && <div className="mb-3 text-sm text-fail">{error}</div>}
            {turn.isPending && <div className="text-sm text-muted">Thinking…</div>}

            {/* Empty-conversation onboarding: concrete example prompts a
                first-time user can click to see what they can ask (story 30). */}
            {!authoring && !rca && !answer && !turn.isPending &&
              starters && starters.length > 0 && (
              <StarterPrompts examples={starters} onPick={(intent) => setText(intent)} />
            )}

            {/* Tool-call transparency (#65): the tools the agent ran this turn,
                each a collapsed one-liner expandable to its args/result. Shown
                before the answer so it reads as "it looked, then it answered." */}
            {toolCalls.length > 0 && <ToolCallList calls={toolCalls} />}

            {authoring && <AuthoringHandoff onOpen={() => openEditor(authoring)} />}
            {rca && <RcaView rca={rca} />}
            {!authoring && !rca && answer && <AnswerView answer={answer} />}

            {/* Dynamic follow-up buttons (#65), rendered from whatever the turn's
                tools made relevant — generic, not a per-capability button set. */}
            {nextActions.length > 0 && (
              <NextActions actions={nextActions} onRun={runNextAction} />
            )}
          </div>

          <div className="border-t border-edge p-3">
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                className="input min-h-[64px] flex-1"
                placeholder="e.g. which tests cover Unity Catalog grants?"
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask()
                }}
              />
              <button className="btn btn-accent" onClick={ask} disabled={turn.isPending}>
                <Send size="16" />
              </button>
            </div>
          </div>
    </aside>
  )
}

// Onboarding starter prompts (spec #56 story 30): 3-4 curated examples from the
// WorkedExample library. Clicking one populates the input with its intent text —
// nothing is asked or saved until the user hits send.
function StarterPrompts({
  examples,
  onPick,
}: {
  examples: WorkedExample[]
  onPick: (intent: string) => void
}) {
  return (
    <div className="mb-3">
      <div className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-wide text-muted">
        <Lightbulb size="12" /> Try asking
      </div>
      <div className="space-y-2">
        {examples.slice(0, 4).map((ex) => (
          <button
            key={ex.id}
            className="w-full rounded-lg border border-edge bg-panel p-2 text-left text-sm text-gray-200 hover:border-accent hover:text-white"
            onClick={() => onPick(ex.intent)}
          >
            {ex.intent}
          </button>
        ))}
      </div>
    </div>
  )
}

// Tool-call transparency (#65, spec #56 stories 3-5): each tool the agent called
// renders as a collapsed one-line indicator ("✓ Called `name`"), expandable to
// show the exact args + result so the answer isn't a black box. Native
// <details> keeps it accessible and collapsed-by-default with no extra state.
function ToolCallList({ calls }: { calls: ToolCall[] }) {
  return (
    <div className="mb-3 space-y-1">
      {calls.map((call, i) => (
        <details key={i} className="group rounded-lg border border-edge bg-panel">
          <summary className="flex cursor-pointer list-none items-center gap-2 p-2 text-xs text-gray-200">
            <ChevronRight
              size="12"
              className="shrink-0 text-muted transition group-open:rotate-90"
            />
            <Check size="12" className="shrink-0 text-pass" />
            <span>
              Called <code className="font-mono text-info">{call.name}</code>
            </span>
          </summary>
          <div className="border-t border-edge p-2">
            <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Arguments</div>
            <pre className="mb-2 overflow-x-auto rounded bg-ink p-2 text-[11px] text-gray-300">
              {JSON.stringify(call.arguments ?? {}, null, 2)}
            </pre>
            <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Result</div>
            <pre className="max-h-52 overflow-auto rounded bg-ink p-2 text-[11px] text-gray-300">
              {JSON.stringify(call.result ?? null, null, 2)}
            </pre>
          </div>
        </details>
      ))}
    </div>
  )
}

// Dynamic next-action buttons (#65, spec #56 stories 6/7): rendered from whatever
// the turn's tools made relevant (server-derived, registry-driven), so a new tool
// contributes its own buttons without a per-capability change here.
function NextActions({
  actions,
  onRun,
}: {
  actions: NextAction[]
  onRun: (a: NextAction) => void
}) {
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {actions.map((a, i) => (
        <button key={i} className="btn text-sm" onClick={() => onRun(a)}>
          {a.kind === 'author' ? <Wand2 size="14" /> : <ArrowUpRight size="14" />}
          {a.label}
        </button>
      ))}
    </div>
  )
}

// A request to write a test: the editor's assistant writes the script with you,
// beside the code, so the handoff carries the sentence over.
function AuthoringHandoff({ onOpen }: { onOpen: () => void }) {
  return (
    <div className="card">
      <div className="mb-1 flex items-center gap-2 card-title">
        <Code2 size="14" /> Write it in the test editor
      </div>
      <p className="mb-3 text-sm text-gray-200">
        Tests are Python scripts. The editor's assistant will draft this one, check every SDK call,
        and dry-run it with you before anything is saved.
      </p>
      <button className="btn btn-accent w-full justify-center" onClick={onOpen}>
        <Code2 size="16" /> Open in the test editor
      </button>
    </div>
  )
}

// A plain query answer (e.g. "do we have a suite for X?") — no draft, no proposal.
function AnswerView({ answer }: { answer: string }) {
  return (
    <div className="card">
      <p className="text-sm text-gray-200">{answer}</p>
    </div>
  )
}

function RcaView({ rca }: { rca: RcaSuggestion }) {
  return (
    <div className="card">
      <div className="mb-1 flex items-center gap-2 card-title">
        <Search size="14" /> Suggested cause
        <span className="badge ml-auto bg-panel text-warn">advisory</span>
      </div>
      <p className="text-sm text-gray-200">{rca.suggested_cause}</p>
      <div className="mt-2 flex items-center gap-2 text-xs text-muted">
        <span className="badge bg-ink text-info">lane: {rca.suggested_lane}</span>
        <span className="badge bg-ink text-muted">confidence: {rca.confidence}</span>
      </div>
      <p className="mt-2 text-[11px] text-muted">
        A suggestion only — you stay the decision-maker (RCA never asserts).
      </p>
    </div>
  )
}
