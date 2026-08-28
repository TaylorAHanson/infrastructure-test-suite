import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, History, Sparkles, Star, Undo2 } from 'lucide-react'

import { PageHeader } from '../components/PageHeader'
import { useDeclarePageContext } from '../lib/assistantContext'
import {
  useAcceptTest,
  usePromoteExample,
  useRollbackTest,
  useStudioTests,
  useTestVersions,
} from '../hooks/queries'
import type { StudioTest } from '../types'

// Test Studio (ADR-0006 §8): the authoring surface with a Draft → accept →
// Active lifecycle, a version history with rollback, and a derivation panel that
// makes an AI-produced Test auditable by a stranger. Authoring itself happens in
// the app-wide Assistant shelf; this page manages what the shelf produced.
export default function TestStudio() {
  const { data: tests } = useStudioTests()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const selected = useMemo(
    () => tests?.find((t) => t.id === selectedId) ?? tests?.[0] ?? null,
    [tests, selectedId],
  )
  useDeclarePageContext({ page: 'test-studio', test_id: selected?.id ?? null })

  const drafts = (tests ?? []).filter((t) => t.status === 'draft')
  const active = (tests ?? []).filter((t) => t.status === 'active')

  return (
    <div>
      <PageHeader
        title="Test Studio"
        subtitle="Draft → accept → Active. Author in one sentence via the Assistant shelf; review, version, and promote here."
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <TestGroup title="Drafts" hint="never run until accepted" tests={drafts}
                     selectedId={selected?.id} onSelect={setSelectedId} />
          <TestGroup title="Active" tests={active}
                     selectedId={selected?.id} onSelect={setSelectedId} />
        </div>
        {selected ? <TestDetail test={selected} /> : (
          <div className="card text-sm text-muted">No tests yet — open the Assistant shelf to author one.</div>
        )}
      </div>
    </div>
  )
}

function TestGroup({
  title, hint, tests, selectedId, onSelect,
}: {
  title: string
  hint?: string
  tests: StudioTest[]
  selectedId?: string
  onSelect: (id: string) => void
}) {
  return (
    <div className="card">
      <div className="mb-2 flex items-center gap-2 card-title">
        {title}
        <span className="badge bg-panel text-muted">{tests.length}</span>
        {hint && <span className="ml-auto text-[10px] normal-case text-muted">{hint}</span>}
      </div>
      <div className="space-y-1">
        {tests.map((t) => (
          <button
            key={t.id}
            onClick={() => onSelect(t.id)}
            className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm ${
              t.id === selectedId ? 'bg-ink-soft text-white' : 'text-gray-200 hover:bg-ink-soft'
            }`}
          >
            {t.origin === 'assistant' && <Sparkles size="13" className="text-accent" />}
            <span className="truncate">{t.name}</span>
            <span className="ml-auto text-[10px] text-muted">v{t.version}</span>
          </button>
        ))}
        {!tests.length && <div className="px-2 text-xs text-muted">none</div>}
      </div>
    </div>
  )
}

function TestDetail({ test }: { test: StudioTest }) {
  const accept = useAcceptTest()
  const rollback = useRollbackTest()
  const promote = usePromoteExample()
  const { data: versions } = useTestVersions(test.id)
  const [promoted, setPromoted] = useState(false)

  const derivation = test.derivation ?? {}
  const chosen = (derivation.chosen_operation ?? null) as
    { service?: string; method?: string; verb?: string; params?: Record<string, unknown> } | null
  const source = (derivation.grounding_source ?? null) as { summary?: string } | null
  const applied = (derivation.applied ?? {}) as {
    counterfactual?: { assertion?: string; why?: string }
    run_as_role?: { role?: string; why?: string }
    suite?: { name?: string; why?: string }
  }

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="mb-2 flex items-center gap-2">
          <span className="text-base font-semibold text-white">{test.name}</span>
          <span className={`badge ${test.status === 'draft' ? 'bg-warn/20 text-warn' : 'bg-pass/20 text-pass'}`}>
            {test.status}
          </span>
          <span className="badge bg-panel text-muted">v{test.version}</span>
          <div className="ml-auto flex gap-2">
            {test.status === 'draft' && (
              <button className="btn btn-accent" onClick={() => accept.mutate(test.id)} disabled={accept.isPending}>
                <CheckCircle2 size="15" /> Accept → Active
              </button>
            )}
            {test.status === 'active' && (
              <button
                className="btn"
                onClick={() => promote.mutate(test.id, { onSuccess: () => setPromoted(true) })}
                disabled={promote.isPending || promoted}
              >
                <Star size="15" /> {promoted ? 'Promoted ✓' : 'Promote to examples'}
              </button>
            )}
          </div>
        </div>
        <p className="text-sm text-muted">{test.description}</p>
        <div className="mt-2 flex flex-wrap gap-1">
          {test.tags.map((t) => <span key={t} className="badge bg-panel text-info">{t}</span>)}
          {test.run_as_role && <span className="badge bg-panel text-accent">run-as: {test.run_as_role}</span>}
        </div>
      </div>

      <div className="card">
        <div className="mb-2 card-title">Checks ({test.members.length})</div>
        <div className="space-y-1">
          {test.members.map((m) => (
            <div key={m.check_id} className="flex items-center gap-2 rounded-lg border border-edge bg-ink p-2 text-sm">
              <span className={`badge ${m.kind === 'counterfactual' ? 'bg-warn/20 text-warn' : 'bg-panel text-muted'}`}>
                {m.kind}
              </span>
              <Link to={`/tests`} className="font-mono text-[11px] text-info">{m.check_id}</Link>
              {m.run_as_role && <span className="ml-auto badge bg-panel text-accent">as {m.run_as_role}</span>}
            </div>
          ))}
        </div>
      </div>

      {test.origin === 'assistant' && (
        <div className="card">
          <div className="mb-2 flex items-center gap-2 card-title">
            <Sparkles size="14" /> Derivation panel (auditable by a stranger)
          </div>
          <dl className="grid grid-cols-[150px_1fr] gap-y-1 text-sm">
            {chosen && (
              <>
                <dt className="text-muted">Chosen operation</dt>
                <dd className="font-mono text-info">{chosen.service}.{chosen.method} ({chosen.verb})</dd>
              </>
            )}
            {chosen?.params && Object.keys(chosen.params).length > 0 && (
              <>
                <dt className="text-muted">Param provenance</dt>
                <dd className="font-mono text-[11px] text-gray-200">{JSON.stringify(chosen.params)}</dd>
              </>
            )}
            {source?.summary && (
              <>
                <dt className="text-muted">Grounding entry</dt>
                <dd className="text-gray-200">{source.summary}</dd>
              </>
            )}
            <dt className="text-muted">Coarse risk tier</dt>
            <dd className="text-gray-200">{String(derivation.coarse_risk_tier ?? '—')}</dd>
            {applied.run_as_role?.role && (
              <>
                <dt className="text-muted">Run-as-role</dt>
                <dd className="text-gray-200">{applied.run_as_role.role}
                  <span className="text-muted"> — {applied.run_as_role.why}</span></dd>
              </>
            )}
            {applied.counterfactual?.assertion && (
              <>
                <dt className="text-muted">Counterfactual</dt>
                <dd className="text-gray-200">“{applied.counterfactual.assertion}”
                  <span className="text-muted"> — {applied.counterfactual.why}</span></dd>
              </>
            )}
            {applied.suite?.name && (
              <>
                <dt className="text-muted">Suite placement</dt>
                <dd className="text-gray-200">{applied.suite.name}
                  <span className="text-muted"> — {applied.suite.why}</span></dd>
              </>
            )}
            <dt className="text-muted">Model available</dt>
            <dd className="text-gray-200">{String(derivation.model_available ?? '—')}</dd>
            <dt className="text-muted">Intent</dt>
            <dd className="text-gray-200">{String(derivation.intent ?? '—')}</dd>
          </dl>
        </div>
      )}

      <div className="card">
        <div className="mb-2 flex items-center gap-2 card-title">
          <History size="14" /> Version history
        </div>
        <div className="space-y-1">
          {(versions ?? []).slice().reverse().map((v) => (
            <div key={v.version} className="flex items-center gap-2 text-sm">
              <span className="badge bg-panel text-muted">v{v.version}</span>
              <span className="text-xs text-muted">{v.actor}</span>
              {v.version !== test.version && (
                <button
                  className="ml-auto text-xs text-accent hover:underline"
                  onClick={() => rollback.mutate({ testId: test.id, version: v.version })}
                >
                  <Undo2 size="12" className="mr-1 inline" /> roll back to v{v.version}
                </button>
              )}
            </div>
          ))}
          {!versions?.length && <div className="text-xs text-muted">no versions recorded</div>}
        </div>
      </div>
    </div>
  )
}
