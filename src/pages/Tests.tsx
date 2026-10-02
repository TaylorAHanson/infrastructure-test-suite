import { Fragment, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Code2,
  History,
  Loader2,
  Pencil,
  Play,
  Plus,
  Search,
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
  useSubmitRun,
  useSuites,
  useTargets,
  useTests,
  useTestVersions,
} from '../hooks/queries'
import { errorMessage } from '../lib/api'
import { ALL_TARGETS } from '../lib/format'
import { GUIDE_WRITING_TESTS } from '../lib/guide'
import type { Check, Depth, StudioTest, Suite } from '../types'

// The one Tests view: the catalog (browse + search), the Draft → Active
// lifecycle and version history, and Suites as a filter over the list. A Test
// is a Python script written in the test editor (/tests/:id/edit), where an
// assistant works beside the code; its checks are the script's `with check(…)`
// blocks. Suites are made by hand here.
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
        subtitle="Python scripts that run against any target. Each check in a script is one result in a run."
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
  const { data: tests } = useTests()
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
          {!tests.length ? (
            <>
              No tests yet. A test is a short Python script: each <code className="font-mono text-info">with check("…"):</code>{' '}
              block in it is one result in a run.{' '}
              <Link to="/tests/new" className="text-accent hover:underline">Write the first one</Link>.
            </>
          ) : q.trim() ? (
            'No tests match your search.'
          ) : (
            <>This suite has no tests. <button className="text-accent hover:underline" onClick={() => suite && setSuiteDialog({ suite })}>Add some</button>.</>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(200px,min(320px,30%))_minmax(0,1fr)]">
          <div className="card space-y-4 p-2 lg:sticky lg:top-0">
            {drafts.length > 0 && (
              <TestGroup title="Drafts" hint="not run until activated" tests={drafts}
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
  hint?: React.ReactNode
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
            <span className="truncate" title={t.name}>{t.name}</span>
            <span className="ml-auto shrink-0 text-[10px] text-muted">{t.members.length} {t.members.length === 1 ? 'check' : 'checks'}</span>
          </button>
        ))}
        {!tests.length && <div className="px-2 pb-1 text-xs text-muted">none</div>}
      </div>
    </div>
  )
}

/** A check's description often just repeats its name. */
function addsToName(text: string | undefined, check: Check): text is string {
  const norm = (s: string) => s.trim().replace(/\s+/g, ' ').toLowerCase()
  return !!text?.trim() && norm(text) !== norm(check.display_name)
}

function checkCalls(check: Check): string[] {
  const calls = check.hints?.calls
  return Array.isArray(calls) ? calls.map(String) : []
}

function TestDetail({ test, checkById }: { test: StudioTest; checkById: Map<string, Check> }) {
  const navigate = useNavigate()
  const accept = useAcceptTest()
  const rollback = useRollbackTest()
  const { data: versions } = useTestVersions(test.id)
  const [confirmRollback, setConfirmRollback] = useState<number | null>(null)
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
              <Code2 size="15" /> Open in editor
            </button>
          </div>
        </div>
        {accept.error && (
          <p className="mt-2 text-sm text-fail">Couldn't activate this test: {errorMessage(accept.error)}</p>
        )}
        {test.description && <p className="mt-2 text-sm text-muted">{test.description}</p>}
        {test.tags.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {test.tags.map((t) => <span key={t} className="badge bg-ink-soft text-muted">{t}</span>)}
          </div>
        )}
        <TestRunControl test={test} />
      </div>

      <div className="card">
        <div className="mb-2 flex items-center gap-2">
          <span className="card-title">Checks ({members.length})</span>
          <span className="text-[11px] text-muted">one per <code className="font-mono">with check(…)</code> block · click one for details</span>
          <Link
            className="ml-auto text-xs text-muted hover:text-white"
            to={GUIDE_WRITING_TESTS}
            title="How test scripts work"
          >
            <CircleHelp size="12" className="mr-1 inline" /> Help
          </Link>
        </div>
        <div className="overflow-x-auto rounded-lg border border-edge">
          <table className="w-full">
            <thead className="bg-ink">
              <tr>
                <th className="th">#</th>
                <th className="th">Check</th>
                <th className="th">Calls</th>
                <th className="th">Kind</th>
                <th className="th">Risk</th>
              </tr>
            </thead>
            <tbody>
              {members.map((m, i) => {
                const check = checkById.get(m.check_id)
                const isCf = m.kind === 'counterfactual' || check?.expectation === 'DENY'
                const calls = check ? checkCalls(check) : []
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
                        {check && addsToName(check.description, check) && (
                          <div className="text-xs text-muted">{check.description}</div>
                        )}
                      </td>
                      <td className="td font-mono text-xs text-info">{check ? calls.join(', ') || '(no calls)' : '-'}</td>
                      <td className="td">
                        <div className="flex flex-wrap gap-1">
                          {isCf ? (
                            <span className="badge bg-info/15 text-info">
                              expect deny{check?.expected_stage ? ` @ ${check.expected_stage}` : ''}
                            </span>
                          ) : (
                            <span className="text-xs text-muted">check</span>
                          )}
                          {!!check?.hints?.advisory && <span className="badge bg-flaky/15 text-flaky">advisory</span>}
                        </div>
                        {m.run_as_role && <div className="mt-1 text-[11px] text-accent">as {m.run_as_role}</div>}
                      </td>
                      <td className="td text-xs text-muted">{check?.risk_tier ?? '-'}</td>
                    </tr>
                    {open && check && (
                      <tr className="bg-ink/40">
                        <td className="td" colSpan={5}><CheckDefinition check={check} /></td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
              {!members.length && (
                <tr>
                  <td className="td text-xs text-muted" colSpan={5}>
                    This script has no checks yet. Open it in the editor and wrap each claim in{' '}
                    <code className="font-mono text-info">with check("…"):</code>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="mb-2 flex items-center gap-2 card-title">
          <History size="14" /> Version history
        </div>
        <div className="space-y-1">
          {(versions ?? []).slice().reverse().map((v) => (
            <div key={v.version} className="flex min-h-[28px] items-center gap-2 text-sm">
              <span className="badge bg-ink-soft text-muted">v{v.version}</span>
              <span className="text-xs text-muted">{v.actor}</span>
              {v.version === test.version ? (
                <span className="ml-auto text-[11px] text-muted">current</span>
              ) : confirmRollback === v.version ? (
                <span className="ml-auto flex items-center gap-2 text-xs">
                  <span className="text-gray-200">
                    Restore v{v.version} as a new version, v{test.version + 1}?
                    {test.status === 'active' && ' The next run uses it.'}
                  </span>
                  <button
                    className="btn px-2 py-0.5 text-xs"
                    disabled={rollback.isPending}
                    onClick={() =>
                      rollback.mutate(
                        { testId: test.id, version: v.version },
                        { onSuccess: () => setConfirmRollback(null) },
                      )
                    }
                  >
                    {rollback.isPending ? <Loader2 size="12" className="animate-spin" /> : <Undo2 size="12" />} Roll back
                  </button>
                  <button
                    className="text-muted hover:text-white"
                    disabled={rollback.isPending}
                    onClick={() => setConfirmRollback(null)}
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <button
                  className="ml-auto text-xs text-accent hover:underline"
                  onClick={() => {
                    rollback.reset()
                    setConfirmRollback(v.version)
                  }}
                >
                  <Undo2 size="12" className="mr-1 inline" /> roll back to v{v.version}
                </button>
              )}
            </div>
          ))}
          {!versions?.length && <div className="text-xs text-muted">no versions recorded</div>}
          {rollback.error && (
            <div className="text-xs text-fail">Couldn't roll back: {errorMessage(rollback.error)}</div>
          )}
        </div>
      </div>
    </div>
  )
}

// Run this one Test for real: a recorded Run through the Run API, like the Runs
// page's launcher but scoped to the Test. Target selection is explicit (no
// default, "All targets" is its own choice); depth defaults to quick (ADR-0007).
function TestRunControl({ test }: { test: StudioTest }) {
  const navigate = useNavigate()
  const { data: targets } = useTargets()
  const submit = useSubmitRun()
  const [targetId, setTargetId] = useState('')
  const [depth, setDepth] = useState<Depth>('quick')
  const enabledTargets = (targets ?? []).filter((t) => t.enabled)
  const target = enabledTargets.find((t) => t.id === targetId)
  const blocked =
    test.status === 'draft'
      ? 'This test is a draft. Activate it to run it, or dry-run it in the editor.'
      : !test.enabled
        ? 'This test is disabled, so it can’t be run.'
        : null

  async function run() {
    if (!targetId || blocked) return
    const where = target ? `${target.name} (${target.safety_class})` : `all ${enabledTargets.length} targets`
    if (depth === 'full' && target?.safety_class !== 'read-only' &&
        !window.confirm(`Full depth makes real create, update and delete calls on ${where}. Run it?`)) {
      return
    }
    try {
      const res = await submit.mutateAsync({
        test_ids: [test.id],
        target_ids: targetId === ALL_TARGETS ? [] : [targetId],
        depth,
        request_source: 'UI_MANUAL',
      })
      navigate(`/runs/${res.run_id}`)
    } catch {
      // shown from submit.error below
    }
  }

  return (
    <div className="mt-3 border-t border-edge pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="card-title mr-1">Run this test</span>
        <select
          className="input max-w-[240px] py-1 text-xs"
          value={targetId}
          onChange={(e) => {
            submit.reset()
            setTargetId(e.target.value)
          }}
          disabled={!!blocked}
          aria-label="Target to run against"
        >
          <option value="" disabled>Pick a target…</option>
          <option value={ALL_TARGETS}>All targets ({enabledTargets.length})</option>
          {enabledTargets.map((t) => (
            <option key={t.id} value={t.id}>{t.name} · {t.safety_class}</option>
          ))}
        </select>
        <div className="flex overflow-hidden rounded-lg border border-edge text-xs" role="group" aria-label="Depth">
          {(['quick', 'full'] as const).map((d) => (
            <button
              key={d}
              onClick={() => setDepth(d)}
              disabled={!!blocked}
              aria-pressed={depth === d}
              className={`px-2 py-1 ${depth === d ? 'bg-ink-soft text-white' : 'text-muted hover:text-white'}`}
              title={d === 'quick' ? 'Read calls only; mutating calls are skipped' : 'Every call the safety class allows'}
            >
              {d}
            </button>
          ))}
        </div>
        <button
          className="btn px-2.5 py-1 text-xs"
          onClick={run}
          disabled={!!blocked || !targetId || submit.isPending}
          title={blocked ?? (targetId ? 'Start a recorded run of this test' : 'Pick a target first')}
        >
          {submit.isPending ? <Loader2 size="13" className="animate-spin" /> : <Play size="13" />} Run
        </button>
        {blocked && <span className="text-xs text-muted">{blocked}</span>}
      </div>
      {submit.error && <p className="mt-2 text-xs text-fail">Couldn't start the run: {errorMessage(submit.error)}</p>}
    </div>
  )
}

// A check's details: the block that declares it, what it calls, and whether a
// failure fails the run.
function CheckDefinition({ check }: { check: Check }) {
  const calls = checkCalls(check)
  const role = check.hints?.role as string | undefined
  return (
    <dl className="grid grid-cols-[150px_1fr] gap-y-1 text-sm">
      {addsToName(check.description, check) && (
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

// -------------------------------------------------------------- Reference tab
// The fixed vocabulary every result is read against: the canonical stage
// spine and the (extensible) failure taxonomy, both served by /admin/settings.
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
          Every check result maps onto this spine. A failure up to AUTHORIZE (blue) is a connectivity
          problem; a failure at OPERATE / VERIFY (amber) is functional.
        </p>
      </div>

      <div className="card">
        <div className="card-title mb-2">Failure taxonomy</div>
        <div className="overflow-x-auto rounded-lg border border-edge">
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
    </div>
  )
}
