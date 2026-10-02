import { Fragment, lazy, Suspense, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Code2,
  Copy,
  FileCode,
  History,
  Pencil,
  Plus,
  Search,
  Sparkles,
  Undo2,
} from 'lucide-react'

import { PageHeader } from '../components/PageHeader'
import SuiteDialog from '../components/SuiteDialog'
import { useDeclarePageContext } from '../lib/assistantContext'
import {
  useAcceptTest,
  useAdminSettings,
  useChecks,
  useRollbackTest,
  useStudioTests,
  useSuites,
  useTestScript,
  useTestVersions,
} from '../hooks/queries'
import { errorMessage } from '../lib/api'
import { statusClass } from '../lib/format'
import { GUIDE_WRITING_TESTS } from '../lib/guide'
import type { Check, Operation, StudioTest, Suite } from '../types'

const CodeEditor = lazy(() => import('../components/CodeEditor'))

// The one Tests view: the catalog (browse + search), the Draft → Active
// lifecycle and version history, and Suites as a filter over the list. A Test
// is a Python script written in the test editor (/tests/:id/edit), where an
// assistant works beside the code. Suites are made by hand here.
// The selected test, suite filter and tab live in the URL so every view is
// linkable and the back button behaves.
export default function Tests() {
  const [params, setParams] = useSearchParams()
  const view = params.get('view') === 'reference' ? 'reference' : 'tests'

  const setParam = (key: string, value: string | null) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value)
      else next.delete(key)
      return next
    }, { replace: key !== 'view' })

  return (
    <div>
      <PageHeader
        title="Tests"
        subtitle="Target-agnostic Tests. Each is a Python script whose checks are its results."
        actions={
          <Link className="btn btn-accent" to="/tests/new">
            <Plus size="15" /> New test
          </Link>
        }
      />

      <div role="tablist" className="mb-4 flex gap-1 border-b border-edge">
        {(['tests', 'reference'] as const).map((v) => (
          <button
            key={v}
            role="tab"
            aria-selected={view === v}
            onClick={() => setParam('view', v === 'tests' ? null : v)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium capitalize transition ${
              view === v ? 'border-accent text-white' : 'border-transparent text-muted hover:text-white'
            }`}
          >
            {v}
          </button>
        ))}
      </div>

      {view === 'tests' ? <TestsView params={params} setParam={setParam} /> : <ReferenceView />}
    </div>
  )
}

// ------------------------------------------------------------------ Tests tab
function TestsView({
  params,
  setParam,
}: {
  params: URLSearchParams
  setParam: (key: string, value: string | null) => void
}) {
  const { data: tests } = useStudioTests()
  const { data: suites } = useSuites()
  const { data: checks } = useChecks()
  const [q, setQ] = useState('')
  const [suiteDialog, setSuiteDialog] = useState<{ suite: Suite | null } | null>(null)

  const suiteId = params.get('suite')
  const suite = suites?.find((s) => s.id === suiteId) ?? null

  const checkById = useMemo(() => new Map((checks ?? []).map((c) => [c.id, c])), [checks])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const inSuite = suite ? new Set(suite.test_ids) : null
    return (tests ?? []).filter(
      (t) =>
        (!inSuite || inSuite.has(t.id)) &&
        (!needle ||
          t.name.toLowerCase().includes(needle) ||
          t.tags.some((tag) => tag.toLowerCase().includes(needle))),
    )
  }, [tests, suite, q])

  const selected = filtered.find((t) => t.id === params.get('test')) ?? filtered[0] ?? null
  useDeclarePageContext({ page: 'tests', test_id: selected?.id ?? null, suite_id: suite?.id ?? null })

  const drafts = filtered.filter((t) => t.status === 'draft')
  const active = filtered.filter((t) => t.status !== 'draft')

  return (
    <>
      <div className="mb-4 space-y-3">
        <label className="relative block">
          <Search size="14" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            className="input w-full pl-9"
            placeholder="Search tests by name or tag…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <SuiteChips
          suites={suites ?? []}
          totalTests={tests?.length ?? 0}
          selectedId={suite?.id ?? null}
          onSelect={(id) => setParam('suite', id)}
          onNew={() => setSuiteDialog({ suite: null })}
        />
        {suite && (
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
            <span>{suite.description || 'No description.'}</span>
            {suite.deterministic_only && (
              <span className="badge bg-pass/15 text-pass">deterministic-only</span>
            )}
            {!suite.enabled && <span className="badge bg-ink-soft text-muted">disabled</span>}
            <button
              className="ml-auto inline-flex items-center gap-1 text-xs text-accent hover:underline"
              onClick={() => setSuiteDialog({ suite })}
            >
              <Pencil size="11" /> Edit suite
            </button>
          </div>
        )}
      </div>

      {!tests ? null : !filtered.length ? (
        <div className="card text-sm text-muted">
          {tests.length ? 'No tests match.' : (
            <>No tests yet. <Link to="/tests/new" className="text-accent hover:underline">Write the first one</Link>.</>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[320px_1fr]">
          <div className="card space-y-4 p-2 lg:sticky lg:top-0">
            {drafts.length > 0 && (
              <TestGroup title="Drafts" hint="not run until accepted" tests={drafts}
                         selectedId={selected?.id} onSelect={(id) => setParam('test', id)} />
            )}
            <TestGroup title="Active" tests={active}
                       selectedId={selected?.id} onSelect={(id) => setParam('test', id)} />
          </div>
          {selected && <TestDetail key={selected.id} test={selected} checkById={checkById} />}
        </div>
      )}
      {suiteDialog && (
        <SuiteDialog
          suite={suiteDialog.suite}
          tests={tests ?? []}
          checkById={checkById}
          onClose={() => setSuiteDialog(null)}
          onSaved={(id) => {
            setSuiteDialog(null)
            setParam('suite', id)
          }}
        />
      )}
    </>
  )
}

function SuiteChips({
  suites, totalTests, selectedId, onSelect, onNew,
}: {
  suites: Suite[]
  totalTests: number
  selectedId: string | null
  onSelect: (id: string | null) => void
  onNew: () => void
}) {
  const chip = (active: boolean) =>
    `inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
      active ? 'border-accent bg-accent/15 text-white' : 'border-edge text-muted hover:border-accent hover:text-white'
    }`
  return (
    <div className="flex flex-wrap items-center gap-2" aria-label="Filter by suite">
      <button className={chip(!selectedId)} onClick={() => onSelect(null)}>
        All tests <span className="text-muted">{totalTests}</span>
      </button>
      {suites.map((s) => (
        <button key={s.id} className={chip(s.id === selectedId)} onClick={() => onSelect(s.id)}>
          {s.name.replace(/ Suite$/, '')} <span className="text-muted">{s.test_ids.length}</span>
        </button>
      ))}
      <button className={`${chip(false)} border-dashed`} onClick={onNew}>
        <Plus size="12" /> New suite
      </button>
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
    <div>
      <div className="mb-1 flex items-center gap-2 px-2 pt-1 card-title">
        {title}
        <span className="text-muted">{tests.length}</span>
        {hint && <span className="ml-auto text-[10px] normal-case tracking-normal">{hint}</span>}
      </div>
      <div className="space-y-0.5">
        {tests.map((t) => (
          <button
            key={t.id}
            onClick={() => onSelect(t.id)}
            className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition ${
              t.id === selectedId ? 'bg-ink-soft text-white' : 'text-gray-300 hover:bg-ink-soft hover:text-white'
            }`}
          >
            {t.script ? (
              <Code2 size="13" className="shrink-0 text-info" />
            ) : (
              t.origin === 'assistant' && <Sparkles size="13" className="shrink-0 text-accent" />
            )}
            <span className="truncate">{t.name}</span>
            <span className="ml-auto shrink-0 text-[10px] text-muted">{t.members.length} {t.members.length === 1 ? 'check' : 'checks'}</span>
          </button>
        ))}
        {!tests.length && <div className="px-2 pb-1 text-xs text-muted">none</div>}
      </div>
    </div>
  )
}

function isScriptCheck(check?: Check): boolean {
  return check?.handler_key === 'script'
}

function scriptCalls(check: Check): string[] {
  const calls = check.hints?.calls
  return Array.isArray(calls) ? calls.map(String) : []
}

function opLabel(op?: Operation, check?: Check): string {
  if (check && isScriptCheck(check)) {
    const calls = scriptCalls(check)
    return calls.length ? calls.join(', ') : '(no calls)'
  }
  if (!op) return '-'
  const call = [op.service, op.method].filter(Boolean).join('.')
  return op.verb ? `${op.verb.toUpperCase()} ${call || '(sdk)'}` : call || '(sdk)'
}

function TestDetail({ test, checkById }: { test: StudioTest; checkById: Map<string, Check> }) {
  const navigate = useNavigate()
  const accept = useAcceptTest()
  const rollback = useRollbackTest()
  const { data: versions } = useTestVersions(test.id)
  const [showScript, setShowScript] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const toggle = (checkId: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (!next.delete(checkId)) next.add(checkId)
      return next
    })
  const members = [...test.members].sort((a, b) => a.sort_order - b.sort_order)

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold text-white">{test.name}</h2>
          <span className={`badge ${test.status === 'draft' ? 'bg-warn/20 text-warn' : 'bg-pass/20 text-pass'}`}>
            {test.status}
          </span>
          <span className="badge bg-ink-soft text-muted">v{test.version}</span>
          {test.run_as_role && <span className="badge bg-ink-soft text-accent">run-as: {test.run_as_role}</span>}
          <div className="ml-auto flex gap-2">
            {test.status === 'draft' && (
              <button className="btn" onClick={() => accept.mutate(test.id)} disabled={accept.isPending}>
                <CheckCircle2 size="15" /> Accept → Active
              </button>
            )}
            <button className="btn btn-accent" onClick={() => navigate(`/tests/${test.id}/edit`)}>
              <Code2 size="15" /> {test.script ? 'Open in editor' : 'Edit as script'}
            </button>
          </div>
        </div>
        {test.description && <p className="mt-2 text-sm text-muted">{test.description}</p>}
        {test.tags.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {test.tags.map((t) => <span key={t} className="badge bg-ink-soft text-muted">{t}</span>)}
          </div>
        )}
      </div>

      <div className="card">
        <div className="mb-2 flex items-center gap-2">
          <span className="card-title">Checks ({members.length})</span>
          <span className="text-[11px] text-muted">click a check for its full definition</span>
          <Link
            className="ml-auto text-xs text-muted hover:text-white"
            to={GUIDE_WRITING_TESTS}
            title="How test scripts work"
          >
            <CircleHelp size="12" className="mr-1 inline" /> Help
          </Link>
          <button className="text-xs text-accent hover:underline" onClick={() => setShowScript((v) => !v)}>
            <FileCode size="12" className="mr-1 inline" /> {showScript ? 'Hide script' : 'View script'}
          </button>
        </div>
        {showScript && <TestScriptView test={test} />}
        <div className="overflow-hidden rounded-lg border border-edge">
          <table className="w-full">
            <thead className="bg-ink">
              <tr>
                <th className="th">#</th>
                <th className="th">Check</th>
                <th className="th">Operation</th>
                <th className="th">Evaluator</th>
                <th className="th">Kind</th>
                <th className="th">Risk</th>
              </tr>
            </thead>
            <tbody>
              {members.map((m, i) => {
                const check = checkById.get(m.check_id)
                const isCf = m.kind === 'counterfactual' || check?.expectation === 'DENY'
                const open = expanded.has(m.check_id)
                return (
                  <Fragment key={m.check_id}>
                    <tr
                      className={`border-t border-edge/50 align-top ${check ? 'cursor-pointer hover:bg-ink-soft/50' : ''}`}
                      onClick={check ? () => toggle(m.check_id) : undefined}
                      aria-expanded={check ? open : undefined}
                    >
                      <td className="td text-xs text-muted">
                        <span className="inline-flex items-center gap-1">
                          {check && (open ? <ChevronDown size="12" /> : <ChevronRight size="12" />)}
                          {i + 1}
                        </span>
                      </td>
                      <td className="td">
                        <div className="text-sm text-white">{check?.display_name ?? m.check_id}</div>
                        {check?.assertion && <div className="text-xs text-muted">{check.assertion}</div>}
                      </td>
                      <td className="td font-mono text-xs text-info">{opLabel(check?.operation, check)}</td>
                      <td className="td">
                        {check?.hints?.advisory ? (
                          <span className="badge bg-flaky/15 text-flaky">advisory</span>
                        ) : (
                          check && <span className={`badge ${statusClass(check.evaluator)}`}>{check.evaluator}</span>
                        )}
                      </td>
                      <td className="td">
                        {isCf ? (
                          <span className="badge bg-info/15 text-info">
                            counterfactual{check?.expected_stage ? ` @ ${check.expected_stage}` : ''}
                          </span>
                        ) : (
                          <span className="text-xs text-muted">assertion</span>
                        )}
                        {m.run_as_role && <div className="mt-1 text-[11px] text-accent">as {m.run_as_role}</div>}
                      </td>
                      <td className="td text-xs text-muted">{check?.risk_tier ?? '-'}</td>
                    </tr>
                    {open && check && (
                      <tr className="bg-ink/40">
                        <td className="td" colSpan={6}><CheckDefinition check={check} /></td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
              {!members.length && (
                <tr><td className="td text-xs text-muted" colSpan={6}>This Test has no Checks.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {test.origin === 'assistant' && <Derivation test={test} />}

      <div className="card">
        <div className="mb-2 flex items-center gap-2 card-title">
          <History size="14" /> Version history
        </div>
        <div className="space-y-1">
          {(versions ?? []).slice().reverse().map((v) => (
            <div key={v.version} className="flex items-center gap-2 text-sm">
              <span className="badge bg-ink-soft text-muted">v{v.version}</span>
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

// A Check's full definition — what it calls, with which params, and how the
// result is judged — for every Test, not just Assistant-authored ones (#117).
function CheckDefinition({ check }: { check: Check }) {
  if (isScriptCheck(check)) return <ScriptCheckDefinition check={check} />
  const params = check.operation?.params ?? {}
  const hasHints = Object.keys(check.hints ?? {}).length > 0
  return (
    <dl className="grid grid-cols-[150px_1fr] gap-y-1 text-sm">
      {check.description && (
        <>
          <dt className="text-muted">Description</dt>
          <dd className="text-gray-200">{check.description}</dd>
        </>
      )}
      <dt className="text-muted">Handler</dt>
      <dd className="font-mono text-xs text-gray-200">{check.handler_key}</dd>
      <dt className="text-muted">Operation</dt>
      <dd className="font-mono text-xs text-info">{opLabel(check.operation)}</dd>
      <dt className="text-muted">Params</dt>
      <dd>
        {Object.keys(params).length ? (
          <pre className="overflow-x-auto rounded bg-ink p-2 text-[11px] text-gray-200">
            {JSON.stringify(params, null, 2)}
          </pre>
        ) : (
          <span className="text-xs text-muted">none</span>
        )}
      </dd>
      <dt className="text-muted">Assertion</dt>
      <dd className="text-gray-200">{check.assertion || '—'}</dd>
      <dt className="text-muted">Expectation</dt>
      <dd className="text-gray-200">
        {check.expectation === 'DENY'
          ? `denied${check.expected_stage ? ` at ${check.expected_stage}` : ''}`
          : 'succeeds'}
      </dd>
      <dt className="text-muted">Evaluator</dt>
      <dd className="text-gray-200">
        {check.evaluator}
        <span className="text-muted">
          {' — '}{check.compilable ? 'compiled to a structured check' : 'not compilable; judged agentically'}
        </span>
      </dd>
      {hasHints && (
        <>
          <dt className="text-muted">Hints</dt>
          <dd className="font-mono text-[11px] text-gray-200">{JSON.stringify(check.hints)}</dd>
        </>
      )}
      <dt className="text-muted">Check id</dt>
      <dd className="font-mono text-xs text-muted">{check.id} · v{check.version}</dd>
    </dl>
  )
}

function ScriptCheckDefinition({ check }: { check: Check }) {
  const calls = scriptCalls(check)
  const role = check.hints?.role as string | undefined
  return (
    <dl className="grid grid-cols-[150px_1fr] gap-y-1 text-sm">
      {check.description && (
        <>
          <dt className="text-muted">Description</dt>
          <dd className="text-gray-200">{check.description}</dd>
        </>
      )}
      <dt className="text-muted">Defined in</dt>
      <dd className="text-gray-200">
        {check.expectation === 'DENY' ? 'with expect_deny(…)' : 'with check(…)'} in the test script
        {check.expectation === 'DENY' && check.expected_stage ? `, denied at ${check.expected_stage}` : ''}
      </dd>
      <dt className="text-muted">Calls</dt>
      <dd className="font-mono text-xs text-info">{calls.length ? calls.join(', ') : 'none'}</dd>
      {role && (
        <>
          <dt className="text-muted">Runs as</dt>
          <dd className="text-accent">{role}</dd>
        </>
      )}
      <dt className="text-muted">Verdict</dt>
      <dd className="text-gray-200">
        {check.hints?.advisory ? 'advisory — reported, never gates the run' : 'gating — a failure fails the run'}
      </dd>
      <dt className="text-muted">Check id</dt>
      <dd className="font-mono text-xs text-muted">{check.id} · v{check.version}</dd>
    </dl>
  )
}

// The Test's script, read-only with copy. Tests that predate scripts show the
// script they would convert to.
function TestScriptView({ test }: { test: StudioTest }) {
  const { data, isLoading, error } = useTestScript(test.id)
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const copy = () => {
    if (!data) return
    // The clipboard API can be denied (no permission, non-secure context);
    // say so rather than fail silently — the text stays selectable.
    navigator.clipboard.writeText(data.script).then(
      () => setCopyState('copied'),
      () => setCopyState('failed'),
    ).finally(() => setTimeout(() => setCopyState('idle'), 1500))
  }
  if (isLoading) return <div className="mb-3 text-xs text-muted">Loading…</div>
  if (error || !data) return <div className="mb-3 text-xs text-fail">Couldn't load the script: {errorMessage(error)}</div>
  return (
    <div className="mb-3">
      {data.converted && (
        <div className="mb-2 text-[11px] text-muted">
          This test predates scripts; this is the script it converts to when you edit it.
        </div>
      )}
      <div className="relative h-96 overflow-hidden rounded-lg border border-edge">
        <button className="btn absolute right-2 top-2 z-10 px-2 py-1 text-xs" onClick={copy}>
          <Copy size="12" /> {copyState === 'copied' ? 'Copied ✓' : copyState === 'failed' ? 'Copy failed — select the text' : 'Copy'}
        </button>
        <Suspense fallback={<div className="p-3 text-xs text-muted">Loading…</div>}>
          <CodeEditor value={data.script} readOnly />
        </Suspense>
      </div>
    </div>
  )
}

// The derivation panel (ADR-0006): how the Assistant built this Test, so a
// stranger can audit it.
function Derivation({ test }: { test: StudioTest }) {
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
    <div className="card">
      <div className="mb-2 flex items-center gap-2 card-title">
        <Sparkles size="14" /> How this test was built
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
  )
}

// -------------------------------------------------------------- Reference tab
// The fixed vocabulary every result is read against: the canonical stage
// spine, the (extensible) failure taxonomy, and the registered handlers. All
// served by /admin/settings.
interface CanonicalStage {
  id: string
  label: string
  connectivity: boolean
}
interface FailureBucket {
  id: string
  connectivity: boolean
  stage?: string | null
}
interface AdminSettings {
  registered_handler_keys?: string[]
  canonical_stages?: CanonicalStage[]
  failure_taxonomy?: FailureBucket[]
}

function ReferenceView() {
  const { data } = useAdminSettings()
  const s = (data ?? {}) as AdminSettings
  const kindBadge = (connectivity: boolean) =>
    `badge ${connectivity ? 'bg-info/15 text-info' : 'bg-warn/15 text-warn'}`

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="card-title mb-2">Stage spine</div>
        <ol className="flex flex-wrap items-center gap-1 text-xs">
          {(s.canonical_stages ?? []).map((st, i) => (
            <li key={st.id} className="flex items-center gap-1">
              <span className={kindBadge(st.connectivity)}>{st.label ?? st.id}</span>
              {i < (s.canonical_stages?.length ?? 0) - 1 && <span className="text-muted">→</span>}
            </li>
          ))}
        </ol>
        <p className="mt-2 text-xs text-muted">
          Every Check result maps onto this spine. A failure up to AUTHORIZE (blue) is a connectivity
          problem; a failure at OPERATE / VERIFY (amber) is functional.
        </p>
      </div>

      <div className="card">
        <div className="card-title mb-2">Failure taxonomy</div>
        <div className="overflow-hidden rounded-lg border border-edge">
          <table className="w-full">
            <thead className="bg-ink">
              <tr>
                <th className="th">Bucket</th>
                <th className="th">Stage</th>
                <th className="th">Kind</th>
              </tr>
            </thead>
            <tbody>
              {(s.failure_taxonomy ?? []).map((b) => (
                <tr key={b.id} className="border-t border-edge/50">
                  <td className="td font-mono text-xs text-gray-200">{b.id}</td>
                  <td className="td text-xs text-muted">{b.stage ?? '-'}</td>
                  <td className="td">
                    <span className={kindBadge(b.connectivity)}>{b.connectivity ? 'connectivity' : 'functional'}</span>
                  </td>
                </tr>
              ))}
              {!s.failure_taxonomy?.length && (
                <tr><td className="td text-sm text-muted" colSpan={3}>No taxonomy loaded.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="card-title mb-2">Handlers</div>
        <div className="flex flex-wrap gap-2">
          {(s.registered_handler_keys ?? []).map((k) => (
            <span key={k} className="badge bg-ink-soft font-mono text-info">{k}</span>
          ))}
          {!s.registered_handler_keys?.length && <span className="text-sm text-muted">none</span>}
        </div>
      </div>
    </div>
  )
}
