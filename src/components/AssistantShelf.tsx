import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Sparkles, X, Send, ShieldAlert, Lightbulb, Search, GripVertical,
  Check, ChevronRight, ArrowUpRight, Wand2,
} from 'lucide-react'

import { useAssistantContext } from '../lib/assistantContext'
import {
  useAssistantTurn,
  useBranding,
  useCommitDraft,
  useConfirmCreateSuite,
  useStarterPrompts,
} from '../hooks/queries'
import type { DraftCheckBody } from '../hooks/queries'
import type {
  DraftProposal,
  NextAction,
  RcaSuggestion,
  SuiteProposal,
  ToolCall,
  WorkedExample,
} from '../types'

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
// It hands the harness the page's declared context, then shows an authoring
// Draft (suggest-then-human-confirms) or an advisory RCA suggestion.
export default function AssistantShelf({ open, onOpen, onClose, width, onResizeStart }: AssistantShelfProps) {
  const { pageContext, seedPrompt, openWithPrompt } = useAssistantContext()
  const navigate = useNavigate()
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // Set when a seed request arrives while the input isn't mounted yet; the
  // focus effect below consumes it once the shelf has opened and rendered.
  const pendingFocus = useRef(false)
  const turn = useAssistantTurn()
  const commit = useCommitDraft()
  const confirmSuite = useConfirmCreateSuite()
  const { data: starters } = useStarterPrompts()
  // The Assistant's display name is a branding setting (ADR-0012/0013), default "Assistant".
  const { data: branding } = useBranding()
  const assistantName = branding?.assistant_name || 'Assistant'

  const [text, setText] = useState('')
  const [draft, setDraft] = useState<DraftProposal | null>(null)
  const [rca, setRca] = useState<RcaSuggestion | null>(null)
  const [suiteProposal, setSuiteProposal] = useState<SuiteProposal | null>(null)
  const [answer, setAnswer] = useState<string | null>(null)
  // Tool-call transparency + dynamic follow-up buttons for the last answer (#65).
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([])
  const [nextActions, setNextActions] = useState<NextAction[]>([])
  const [savedId, setSavedId] = useState<string | null>(null)
  const [savedSuiteId, setSavedSuiteId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Which guided-authoring suggestions the human has confirmed (default off:
  // nothing is applied without an explicit confirm — ADR-0006 §5, story 7).
  const [applyCf, setApplyCf] = useState(false)
  const [applyRole, setApplyRole] = useState(false)
  const [applySuite, setApplySuite] = useState(false)

  // A seed request from any entry point (e.g. Test Studio's "New" button) opens
  // the shelf and pre-fills the input with an editable starting sentence — not
  // gated on the conversation being empty, so it works mid-session too. Prior
  // results are cleared so the seeded turn starts clean; we never auto-send, so
  // the human still decides when to ask.
  useEffect(() => {
    if (!seedPrompt) return
    onOpen()
    setText(seedPrompt.text)
    setDraft(null)
    setRca(null)
    setSuiteProposal(null)
    setAnswer(null)
    setToolCalls([])
    setNextActions([])
    setSavedId(null)
    setSavedSuiteId(null)
    setError(null)
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

  async function ask() {
    if (!text.trim()) return
    setError(null)
    setDraft(null)
    setRca(null)
    setSuiteProposal(null)
    setAnswer(null)
    setToolCalls([])
    setNextActions([])
    setSavedId(null)
    setSavedSuiteId(null)
    setApplyCf(false)
    setApplyRole(false)
    setApplySuite(false)
    try {
      const res = await turn.mutateAsync({ turn: text, page_context: pageContext })
      if (res.mode === 'authoring') setDraft(res.draft ?? null)
      else if (res.mode === 'suite_authoring') {
        setSuiteProposal(res.suite_proposal ?? null)
        setAnswer(res.answer ?? null)
      } else if (res.mode === 'query') setAnswer(res.answer ?? null)
      else setRca(res.rca ?? null)
      // Additive: which tools ran + the contextual follow-up buttons (#65).
      setToolCalls(res.tool_calls ?? [])
      setNextActions(res.next_actions ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The Assistant could not respond.')
    }
  }

  async function confirmSuiteProposal() {
    if (!suiteProposal || suiteProposal.action !== 'create') return
    setError(null)
    try {
      const created = await confirmSuite.mutateAsync(suiteProposal)
      setSavedSuiteId(created.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create the Suite.')
    }
  }

  // Run a dynamic next-action button (#65): `author` re-seeds the input with a
  // starting sentence; `navigate` routes the main pane (the shelf stays open, so
  // both are visible — the push-pane layout, #58).
  function runNextAction(action: NextAction) {
    if (action.kind === 'author' && action.seed) openWithPrompt(action.seed)
    else if (action.kind === 'navigate' && action.href) navigate(action.href)
  }

  async function saveDraft() {
    if (!draft || !draft.checks.length) return
    setError(null)

    const checks: DraftCheckBody[] = draft.checks.map((c) => ({
      operation: c.operation,
      handler_key: c.handler_key,
      assertion: c.assertion,
      kind: c.kind,
    }))
    // Apply only the suggestions the human confirmed (suggest-then-confirm).
    const applied: Record<string, unknown> = {}
    const cf = draft.suggested_counterfactual
    if (applyCf && cf) {
      checks.push({
        operation: cf.operation,
        handler_key: cf.handler_key,
        assertion: cf.assertion,
        kind: 'counterfactual',
        counterpart_index: 0,
      })
      applied.counterfactual = { assertion: cf.assertion, why: cf.why }
    }
    const roleRef = draft.suggested_run_as_role?.role
    const runAsRole =
      applyRole && roleRef ? (roleRef.label || roleRef.group_id) : undefined
    if (runAsRole) applied.run_as_role = { role: runAsRole, why: draft.suggested_run_as_role?.why }
    const suiteId = applySuite ? draft.suggested_suite?.suite_id : undefined
    if (suiteId) applied.suite = { suite_id: suiteId, name: draft.suggested_suite?.name,
                                   why: draft.suggested_suite?.why }

    try {
      const created = await commit.mutateAsync({
        name: draft.name || 'Untitled Test',
        description: draft.description,
        checks,
        run_as_role: runAsRole,
        suite_id: suiteId,
        // Persist what was applied + why, so the derivation panel is auditable
        // by a stranger (ADR-0006 §8, user story 11).
        derivation: { ...draft.derivation, applied },
      })
      setSavedId(created.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the Draft.')
    }
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
              Describe a test in one sentence, or ask why a check regressed. Nothing is
              saved or run until you confirm.
            </p>

            {error && <div className="mb-3 text-sm text-fail">{error}</div>}
            {turn.isPending && <div className="text-sm text-muted">Thinking…</div>}

            {/* Empty-conversation onboarding: concrete example prompts a
                first-time user can click to see what they can ask (story 30). */}
            {!draft && !rca && !suiteProposal && !answer && !turn.isPending &&
              starters && starters.length > 0 && (
              <StarterPrompts examples={starters} onPick={(intent) => setText(intent)} />
            )}

            {/* Tool-call transparency (#65): the tools the agent ran this turn,
                each a collapsed one-liner expandable to its args/result. Shown
                before the answer so it reads as "it looked, then it answered." */}
            {toolCalls.length > 0 && <ToolCallList calls={toolCalls} />}

            {draft && (
              <DraftView
                draft={draft}
                savedId={savedId}
                onSave={saveDraft}
                saving={commit.isPending}
                toggles={{ applyCf, applyRole, applySuite }}
                setToggles={{ setApplyCf, setApplyRole, setApplySuite }}
              />
            )}
            {rca && <RcaView rca={rca} />}
            {suiteProposal && (
              <SuiteProposalView
                proposal={suiteProposal}
                answer={answer}
                savedId={savedSuiteId}
                onConfirm={confirmSuiteProposal}
                confirming={confirmSuite.isPending}
              />
            )}
            {!suiteProposal && !draft && !rca && answer && <AnswerView answer={answer} />}

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
                placeholder="e.g. start a cluster under compute-policy P and confirm it's allowed"
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

function riskColor(tier: string): string {
  return tier === 'R0' ? 'text-pass' : tier === 'R3' ? 'text-fail' : 'text-warn'
}

interface Toggles {
  applyCf: boolean
  applyRole: boolean
  applySuite: boolean
}

function DraftView({
  draft,
  savedId,
  onSave,
  saving,
  toggles,
  setToggles,
}: {
  draft: DraftProposal
  savedId: string | null
  onSave: () => void
  saving: boolean
  toggles: Toggles
  setToggles: {
    setApplyCf: (v: boolean) => void
    setApplyRole: (v: boolean) => void
    setApplySuite: (v: boolean) => void
  }
}) {
  if (draft.clarifying_question) {
    return (
      <div className="card">
        <div className="mb-1 flex items-center gap-2 card-title">
          <Lightbulb size="14" /> One question
        </div>
        <p className="text-sm text-gray-200">{draft.clarifying_question}</p>
      </div>
    )
  }
  return (
    <div className="space-y-3">
      <div className="card">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-sm font-semibold text-white">{draft.name}</span>
          <span className={`badge ml-auto ${riskColor(draft.max_risk_tier)}`}>
            max {draft.max_risk_tier}
          </span>
        </div>
        <p className="mb-2 text-xs text-muted">{draft.description}</p>
        {draft.checks.map((c, i) => (
          <div key={i} className="mb-2 rounded-lg border border-edge bg-panel p-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[11px] text-info">
                {c.operation.service}.{c.operation.method}
              </span>
              <span className={`badge ${riskColor(c.risk_tier)}`}>{c.risk_tier}</span>
              <span className="badge bg-ink text-muted">{c.evaluator}</span>
            </div>
            <div className="mt-1 text-sm text-gray-200">“{c.assertion}”</div>
          </div>
        ))}
      </div>

      {draft.refusals.length > 0 && (
        <div className="card border-fail/40 bg-fail/5">
          <div className="mb-1 flex items-center gap-2 card-title text-fail">
            <ShieldAlert size="14" /> Refused (R3 / blocklisted)
          </div>
          {draft.refusals.map((r, i) => (
            <p key={i} className="text-xs text-gray-200">{r.reason}</p>
          ))}
        </div>
      )}

      {(draft.suggested_counterfactual || draft.suggested_suite || draft.suggested_run_as_role) && (
        <div className="card">
          <div className="mb-2 card-title">Guided suggestions (tick to apply on save)</div>
          {draft.suggested_counterfactual && (
            <Suggestion label="Counterfactual" why={draft.suggested_counterfactual.why}
                        checked={toggles.applyCf} onChange={setToggles.setApplyCf}>
              “{draft.suggested_counterfactual.assertion}”
            </Suggestion>
          )}
          {draft.suggested_run_as_role && (
            <Suggestion label="Run-as-role" why={draft.suggested_run_as_role.why}
                        checked={toggles.applyRole} onChange={setToggles.setApplyRole}>
              {draft.suggested_run_as_role.role.label || draft.suggested_run_as_role.role.group_id}
            </Suggestion>
          )}
          {draft.suggested_suite && (
            <Suggestion label="Suite placement" why={draft.suggested_suite.why}
                        checked={toggles.applySuite} onChange={setToggles.setApplySuite}>
              {draft.suggested_suite.name}
            </Suggestion>
          )}
        </div>
      )}

      {draft.checks.length > 0 && (
        <button className="btn btn-accent w-full" onClick={onSave} disabled={saving || !!savedId}>
          {savedId ? 'Saved as Draft ✓ (accept it in Test Studio to run)' : saving ? 'Saving…' : 'Save as Draft'}
        </button>
      )}
    </div>
  )
}

function Suggestion({
  label,
  why,
  children,
  checked,
  onChange,
}: {
  label: string
  why: string
  children: ReactNode
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="mb-2 flex cursor-pointer gap-2 rounded-lg border border-edge bg-ink p-2">
      <input
        type="checkbox"
        className="mt-1"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div>
        <div className="flex items-center gap-2">
          <span className="badge bg-panel text-accent">{label}</span>
          <span className="text-sm text-gray-200">{children}</span>
        </div>
        <div className="mt-1 text-[11px] text-muted">{why}</div>
      </div>
    </label>
  )
}

// The Suites-page capability's proposal (issue #66/#63): a brand-new Suite or a
// change to one on screen — never created/applied until the human confirms.
// Only "create" is confirmable from here; composing an existing Suite (#63)
// still requires the specific test id and is left to a future entry point.
function SuiteProposalView({
  proposal,
  answer,
  savedId,
  onConfirm,
  confirming,
}: {
  proposal: SuiteProposal
  answer: string | null
  savedId: string | null
  onConfirm: () => void
  confirming: boolean
}) {
  const violation = proposal.deterministic_violation
  return (
    <div className="space-y-3">
      {answer && <p className="text-sm text-gray-200">{answer}</p>}
      <div className="card">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-sm font-semibold text-white">
            {proposal.name || `Suite ${proposal.suite_id ?? ''}`}
          </span>
          <span className="badge ml-auto bg-panel text-muted">{proposal.action}</span>
        </div>
        {proposal.description && <p className="mb-2 text-xs text-muted">{proposal.description}</p>}
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="badge bg-ink-soft text-muted">
            {proposal.proposed_test_ids.length} tests
          </span>
          {proposal.proposed_deterministic_only && (
            <span className="badge bg-pass/15 text-pass">deterministic-only</span>
          )}
          {proposal.job_scheduled && (
            <span className="badge bg-warn/15 text-warn">used by nightly job</span>
          )}
        </div>
      </div>

      {violation && (
        <div className="card border-fail/40 bg-fail/5">
          <div className="mb-1 flex items-center gap-2 card-title text-fail">
            <ShieldAlert size="14" /> Refused
          </div>
          <p className="text-xs text-gray-200">{violation}</p>
        </div>
      )}

      {proposal.action === 'create' && !violation && (
        <button className="btn btn-accent w-full" onClick={onConfirm} disabled={confirming || !!savedId}>
          {savedId ? 'Created ✓' : confirming ? 'Creating…' : 'Confirm & create Suite'}
        </button>
      )}
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
