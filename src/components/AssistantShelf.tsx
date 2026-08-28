import { useState } from 'react'
import type { ReactNode } from 'react'
import { Sparkles, X, Send, ShieldAlert, Lightbulb, Search } from 'lucide-react'

import { useAssistantContext } from '../lib/assistantContext'
import { useAssistantTurn, useCommitDraft } from '../hooks/queries'
import type { DraftCheckBody } from '../hooks/queries'
import type { DraftProposal, RcaSuggestion } from '../types'

// The single, app-wide Assistant surface (ADR-0012): a slide-out shelf reachable
// from every page. It hands the harness the page's declared context, then shows
// an authoring Draft (suggest-then-human-confirms) or an advisory RCA suggestion.
export default function AssistantShelf() {
  const [open, setOpen] = useState(false)
  const { pageContext } = useAssistantContext()
  const turn = useAssistantTurn()
  const commit = useCommitDraft()

  const [text, setText] = useState('')
  const [draft, setDraft] = useState<DraftProposal | null>(null)
  const [rca, setRca] = useState<RcaSuggestion | null>(null)
  const [savedId, setSavedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Which guided-authoring suggestions the human has confirmed (default off:
  // nothing is applied without an explicit confirm — ADR-0006 §5, story 7).
  const [applyCf, setApplyCf] = useState(false)
  const [applyRole, setApplyRole] = useState(false)
  const [applySuite, setApplySuite] = useState(false)

  async function ask() {
    if (!text.trim()) return
    setError(null)
    setDraft(null)
    setRca(null)
    setSavedId(null)
    setApplyCf(false)
    setApplyRole(false)
    setApplySuite(false)
    try {
      const res = await turn.mutateAsync({ turn: text, page_context: pageContext })
      if (res.mode === 'authoring') setDraft(res.draft ?? null)
      else setRca(res.rca ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The Assistant could not respond.')
    }
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

  return (
    <>
      {!open && (
        <button
          className="btn btn-accent fixed bottom-6 right-6 z-40 shadow-lg"
          onClick={() => setOpen(true)}
        >
          <Sparkles size="16" />
          Assistant
        </button>
      )}

      {open && (
        <aside className="fixed right-0 top-0 z-50 flex h-full w-[420px] flex-col border-l border-edge bg-ink shadow-2xl">
          <div className="flex items-center gap-2 border-b border-edge p-4">
            <Sparkles size="18" className="text-accent" />
            <span className="text-sm font-semibold text-white">Assistant</span>
            <span className="badge bg-panel text-muted">{pageContext.page || 'app'}</span>
            <button className="ml-auto text-muted hover:text-white" onClick={() => setOpen(false)}>
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
          </div>

          <div className="border-t border-edge p-3">
            <div className="flex items-end gap-2">
              <textarea
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
      )}
    </>
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
