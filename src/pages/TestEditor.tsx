import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  AlertCircle, ArrowLeft, CheckCircle2, CircleHelp, FileDiff, GripVertical, Loader2, Play, RotateCw, Save, Trash2,
} from 'lucide-react'

import CodeEditor, { type CodeEditorHandle, type EditorDiagnostic, type ReviewProgress } from '../components/CodeEditor'
import EditorAssistant, { chatKey, loadChat, saveChat, type ChatEntry } from '../components/editor/EditorAssistant'
import ResultsPanel, { tierClass, type PanelTab } from '../components/editor/ResultsPanel'
import {
  useAcceptTest, useCreateScriptTest, useDeleteTest, useDryRun, useSaveScript, useScriptTemplate,
  useSdkIndex, useTargets, useTest, useTests, useTestScript,
} from '../hooks/queries'
import { useSplit } from '../hooks/useSplit'
import { api, ApiError, errorMessage } from '../lib/api'
import { useDeclarePageContext } from '../lib/assistantContext'
import { GUIDE_WRITING_TESTS } from '../lib/guide'
import type { Depth, DryRunResponse, EditorProposal, ScriptAnalysis, Target } from '../types'

type DryRunState = DryRunResponse & { by: 'you' | 'assistant'; script: string }

const TARGET_KEY = 'editor.target'

function defaultTarget(targets: Target[]): string | null {
  const enabled = targets.filter((t) => t.enabled)
  try {
    const remembered = localStorage.getItem(TARGET_KEY)
    if (remembered && enabled.some((t) => t.id === remembered)) return remembered
  } catch {
    // ignore
  }
  return (enabled.find((t) => t.safety_class === 'read-only') ?? enabled[0])?.id ?? null
}

// The route wrapper: a test created from /tests/new keeps the same editor
// instance (buffer, chat, dry run) when the URL moves to /tests/:id/edit.
export default function TestEditorPage() {
  const { testId } = useParams()
  const created = useRef<string | null>(null)
  const key = testId && testId === created.current ? 'new' : (testId ?? 'new')
  return <TestEditor key={key} testId={testId ?? null} onCreated={(id) => (created.current = id)} />
}

function TestEditor({ testId, onCreated }: { testId: string | null; onCreated: (id: string) => void }) {
  const navigate = useNavigate()
  const [search, setSearch] = useSearchParams()
  const testsQ = useTests()
  const tests = testsQ.data
  const test = tests?.find((t) => t.id === testId) ?? null
  // Not in the list: look it up directly to tell a retired Test from a missing one.
  const missingQ = useTest(testId ?? undefined, !!tests && !test)
  const scriptQ = useTestScript(testId ?? undefined)
  const template = useScriptTemplate(!testId)
  const { data: sdk } = useSdkIndex()
  const { data: targets } = useTargets()
  const create = useCreateScriptTest()
  const save = useSaveScript()
  const accept = useAcceptTest()
  const remove = useDeleteTest()
  const dry = useDryRun()

  useDeclarePageContext({ page: 'test-editor', test_id: testId })

  const [buffer, setBuffer] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [savedMeta, setSavedMeta] = useState({ name: '', description: '', role: '' })
  const [analysis, setAnalysis] = useState<ScriptAnalysis | null>(null)
  // `doc` has every proposed change not yet undone; `original` the buffer plus only the kept ones.
  const [review, setReview] = useState<{
    proposal: EditorProposal; doc: string; original: string; remaining: number; total: number
  } | null>(null)
  const [dryRun, setDryRun] = useState<DryRunState | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [tab, setTab] = useState<PanelTab>('problems')
  const [collapsed, setCollapsed] = useState(false)
  const [targetId, setTargetIdState] = useState<string | null>(null)
  const [depth, setDepth] = useState<Depth>('quick')
  const [role, setRole] = useState('')
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [entries, setEntriesState] = useState<ChatEntry[]>(() => loadChat(testId))
  const editor = useRef<CodeEditorHandle>(null)
  const nameInput = useRef<HTMLInputElement>(null)
  const split = useSplit('editor.split', 0.6)
  const initialAsk = useRef(search.get('ask'))

  // ---- load
  useEffect(() => {
    if (buffer !== null) return
    if (!testId && template.data) {
      setBuffer(template.data.script)
    } else if (testId && scriptQ.data && test) {
      setBuffer(scriptQ.data.script)
      setSaved(scriptQ.data.script)
      setAnalysis(scriptQ.data.analysis)
      setName(test.name)
      setDescription(test.description)
      setSavedMeta({ name: test.name, description: test.description, role: test.run_as_role ?? '' })
      setRole(test.run_as_role ?? '')
    }
  }, [buffer, testId, template.data, scriptQ.data, test])

  useEffect(() => {
    if (targets && targetId === null) setTargetIdState(defaultTarget(targets))
  }, [targets, targetId])

  const setTargetId = (id: string) => {
    setTargetIdState(id || null)
    try {
      localStorage.setItem(TARGET_KEY, id)
    } catch {
      // ignore
    }
  }
  const target = targets?.find((t) => t.id === targetId) ?? null

  const setEntries = useCallback(
    (fn: (prev: ChatEntry[]) => ChatEntry[]) =>
      setEntriesState((prev) => {
        const next = fn(prev)
        saveChat(testId, next)
        return next
      }),
    [testId],
  )

  // ---- live analysis
  const seq = useRef(0)
  useEffect(() => {
    if (buffer === null) return
    const mine = ++seq.current
    const h = setTimeout(() => {
      api
        .post<ScriptAnalysis>('/scripts/analyze', { script: buffer })
        .then((a) => mine === seq.current && setAnalysis(a))
        .catch(() => undefined)
    }, 350)
    return () => clearTimeout(h)
  }, [buffer])

  const dirty = buffer !== null && (buffer !== saved || name !== savedMeta.name ||
    description !== savedMeta.description || role !== savedMeta.role)

  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  // ---- diagnostics: static analysis, plus the last dry run's failures while it still matches the buffer
  const diagnostics = useMemo<EditorDiagnostic[]>(() => {
    const out: EditorDiagnostic[] = [
      ...(analysis?.errors ?? []).map((d) => ({ ...d, severity: 'error' as const })),
      ...(analysis?.warnings ?? []).map((d) => ({ ...d, severity: 'warning' as const })),
    ]
    if (dryRun?.result && dryRun.script === buffer) {
      for (const c of dryRun.result.checks) {
        if (!c.line || c.outcome === 'pass' || c.outcome === 'not_reached') continue
        out.push({
          line: c.line,
          severity: c.outcome === 'fail' || c.outcome === 'error' ? 'error' : 'info',
          message: `Dry run · ${c.name}: ${c.outcome}${c.message ? ` — ${c.message}` : ''}`,
        })
      }
      const err = dryRun.result.error
      if (err?.line) out.push({ line: err.line, severity: 'error', message: `Dry run · ${err.message}` })
    }
    return out
  }, [analysis, dryRun, buffer])

  // ---- actions
  async function doSave() {
    if (buffer === null || create.isPending || save.isPending) return
    if (!name.trim()) {
      setNotice({ kind: 'error', text: 'Give the test a name first.' })
      nameInput.current?.focus()
      return
    }
    if (review) {
      setNotice({ kind: 'error', text: 'Keep or undo the proposed changes before saving.' })
      return
    }
    try {
      const fields = { name: name.trim(), description, script: buffer, run_as_role: role || null }
      let version: number
      if (!testId) {
        // The create's onSuccess has already put the new test in the list, so
        // the editor finds it when the URL moves to /tests/:id/edit.
        const t = await create.mutateAsync(fields)
        onCreated(t.id)
        saveChat(t.id, entries)
        try {
          localStorage.removeItem(chatKey(null))
        } catch {
          // ignore
        }
        version = t.version
        navigate(`/tests/${t.id}/edit`, { replace: true })
      } else {
        const t = await save.mutateAsync({ testId, ...fields })
        version = t.version
      }
      setSaved(buffer)
      setSavedMeta({ name: fields.name, description, role })
      setName(fields.name)
      setNotice({ kind: 'ok', text: `Saved as v${version}` })
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : undefined
      if (detail && typeof detail === 'object' && 'analysis' in detail) {
        setAnalysis((detail as { analysis: ScriptAnalysis }).analysis)
        setTab('problems')
        setCollapsed(false)
      }
      setNotice({ kind: 'error', text: errorMessage(e) })
    }
  }

  async function doRun() {
    if (buffer === null || dry.isPending) return
    setTab('run')
    setCollapsed(false)
    if (!targetId || !target) {
      setRunError('Pick a target to run against.')
      return
    }
    if (depth === 'full' && target.safety_class !== 'read-only' &&
        !window.confirm(`Full depth makes real create, update and delete calls on ${target.name} (${target.safety_class}). Run it?`)) {
      return
    }
    setRunError(null)
    try {
      const res = await dry.mutateAsync({ script: buffer, target_id: targetId, depth, run_as_role: role || null })
      setDryRun({ ...res, by: 'you', script: buffer })
      setAnalysis(res.analysis)
    } catch (e) {
      setRunError(errorMessage(e))
    }
  }

  async function doActivate() {
    if (!testId || dirty) return
    try {
      await accept.mutateAsync(testId)
      setNotice({ kind: 'ok', text: 'Active — it runs with the next scheduled or manual run.' })
    } catch (e) {
      setNotice({ kind: 'error', text: errorMessage(e) })
    }
  }

  async function doDelete() {
    if (!testId || !window.confirm(`Delete the draft "${name}"? This can't be undone.`)) return
    try {
      await remove.mutateAsync(testId)
      try {
        localStorage.removeItem(chatKey(testId))
      } catch {
        // ignore
      }
      navigate('/tests', { replace: true })
    } catch (e) {
      setNotice({ kind: 'error', text: errorMessage(e) })
    }
  }

  function leave() {
    if (dirty && !window.confirm('You have unsaved changes. Leave anyway?')) return
    navigate(testId ? `/tests?test=${testId}` : '/tests')
  }

  // ⌘S / ⌘↵ anywhere on the page (the code editor handles its own and marks the event handled).
  const actions = useRef({ doSave, doRun })
  actions.current = { doSave, doRun }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !(e.metaKey || e.ctrlKey)) return
      if (e.key === 's') {
        e.preventDefault()
        actions.current.doSave()
      } else if (e.key === 'Enter' && !(e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault()
        actions.current.doRun()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (notice?.kind !== 'ok') return
    const h = setTimeout(() => setNotice(null), 3500)
    return () => clearTimeout(h)
  }, [notice])

  const onProposal = useCallback(
    (p: EditorProposal) => setReview({ proposal: p, doc: p.script, original: buffer ?? '', remaining: 0, total: 0 }),
    [buffer],
  )
  const finishReview = useCallback((doc: string) => {
    setBuffer(doc)
    setReview(null)
    editor.current?.focus()
  }, [])
  const reviewTotal = useRef(0)
  reviewTotal.current = review?.total ?? 0
  const onReviewChange = useCallback(
    ({ remaining, doc, original }: ReviewProgress) => {
      // Every change kept or undone: there's nothing left to review.
      if (remaining === 0 && reviewTotal.current > 0) return finishReview(doc)
      setReview((r) => (r ? { ...r, doc, original, remaining, total: Math.max(r.total, remaining) } : r))
    },
    [finishReview],
  )
  const onAgentDryRun = useCallback(
    (d: DryRunResponse) => {
      setDryRun({ ...d, by: 'assistant', script: d.script ?? buffer ?? '' })
      setTab('run')
      setCollapsed(false)
    },
    [buffer],
  )

  // ---- render
  if (testId && tests && !test) {
    if (missingQ.isPending) return <EditorLoading />
    const retired = missingQ.data?.tags.includes('retired')
    return (
      <div className="card text-sm text-muted">
        {retired ? (
          <>
            <span className="text-white">{missingQ.data!.name}</span> was retired: a newer release of the bundled
            catalog replaced it. Its run history is kept, but it no longer runs and isn't shown in the Tests list.{' '}
          </>
        ) : missingQ.error instanceof ApiError && missingQ.error.status === 404 ? (
          "That test doesn't exist. "
        ) : (
          <span className="text-fail">Couldn't load this test: {errorMessage(missingQ.error)}. </span>
        )}
        <Link to="/tests" className="text-accent hover:underline">Back to tests</Link>
      </div>
    )
  }
  if (buffer === null) {
    const err = testId ? (scriptQ.error ?? (tests ? null : testsQ.error)) : template.error
    if (!err) return <EditorLoading />
    return (
      <EditorLoadError
        what={testId ? "this test's script" : 'the starter script'}
        error={err}
        onRetry={() => (testId ? (scriptQ.error ? scriptQ.refetch() : testsQ.refetch()) : template.refetch())}
        onBlank={testId ? undefined : () => setBuffer('')}
      />
    )
  }

  const roles = target?.assumable_roles ?? []
  const saving = create.isPending || save.isPending
  const errorCount = analysis?.errors.length ?? 0

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {/* Header */}
      <div className="flex shrink-0 items-start gap-3">
        <button className="btn mt-0.5 px-2" onClick={leave} title="Back to tests">
          <ArrowLeft size="16" />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <input
              ref={nameInput}
              className="min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-lg font-semibold text-white outline-none hover:border-edge focus:border-accent"
              placeholder="Name this test"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            {test ? (
              <span className={`badge ${test.status === 'draft' ? 'bg-warn/20 text-warn' : 'bg-pass/20 text-pass'}`}>{test.status}</span>
            ) : (
              <span className="badge bg-ink-soft text-muted">new</span>
            )}
            {test && <span className="badge bg-ink-soft text-muted">v{test.version}</span>}
            {dirty && <span className="text-[11px] text-warn">● unsaved</span>}
          </div>
          <input
            className="mt-0.5 w-full rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-xs text-muted outline-none hover:border-edge focus:border-accent focus:text-gray-200"
            placeholder="What does this test prove? (optional)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {notice && (
            <span className={`flex max-w-md items-center gap-1 text-xs ${notice.kind === 'ok' ? 'text-pass' : 'text-fail'}`}>
              {notice.kind === 'ok' ? <CheckCircle2 size="13" /> : <AlertCircle size="13" />}
              <span className="truncate" title={notice.text}>{notice.text}</span>
            </span>
          )}
          {test?.status === 'draft' && (
            <button className="btn px-2 text-muted hover:text-fail" onClick={doDelete} title="Delete this draft" disabled={remove.isPending}>
              <Trash2 size="15" />
            </button>
          )}
          <button className="btn" onClick={doSave} disabled={saving || (!dirty && !!testId)} title="Save (⌘S)">
            {saving ? <Loader2 size="15" className="animate-spin" /> : <Save size="15" />}
            {testId ? 'Save' : 'Create draft'}
          </button>
          {test?.status === 'draft' && (
            <button
              className="btn btn-accent"
              onClick={doActivate}
              disabled={dirty || accept.isPending || errorCount > 0}
              title={dirty ? 'Save first' : 'Make this test part of every run'}
            >
              <CheckCircle2 size="15" /> Activate
            </button>
          )}
        </div>
      </div>

      {test?.status === 'active' && dirty && (
        <div className="shrink-0 text-[11px] text-warn">
          This test is active: saving it changes what the next run executes.
        </div>
      )}

      {/* The shared box: code on the left, the assistant on the right */}
      <div ref={split.container} className="flex min-h-0 flex-1 overflow-hidden rounded-xl border border-edge bg-panel">
        <div className="flex min-w-0 flex-col" style={{ width: `${split.fraction * 100}%` }}>
          {/* Toolbar */}
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-edge px-2 py-1.5">
            <select
              className="input max-w-[220px] py-1 text-xs"
              value={targetId ?? ''}
              onChange={(e) => setTargetId(e.target.value)}
              title="Target to dry-run against"
            >
              <option value="">Pick a target…</option>
              {(targets ?? []).filter((t) => t.enabled).map((t) => (
                <option key={t.id} value={t.id}>{t.name} · {t.safety_class}</option>
              ))}
            </select>
            <div className="flex overflow-hidden rounded-lg border border-edge text-xs">
              {(['quick', 'full'] as const).map((d) => (
                <button
                  key={d}
                  onClick={() => setDepth(d)}
                  className={`px-2 py-1 ${depth === d ? 'bg-ink-soft text-white' : 'text-muted hover:text-white'}`}
                  title={d === 'quick' ? 'Read calls only; mutating calls are skipped' : 'Every call the safety class allows'}
                >
                  {d}
                </button>
              ))}
            </div>
            {roles.length > 0 && (
              <select className="input max-w-[180px] py-1 text-xs" value={role} onChange={(e) => setRole(e.target.value)} title="Default identity for checks that don't name a role">
                <option value="">as the target identity</option>
                {roles.map((r) => (
                  <option key={r.group_id} value={r.label || r.group_id}>as {r.label || r.group_id}</option>
                ))}
                {role && !roles.some((r) => (r.label || r.group_id) === role) && (
                  <option value={role}>as {role} (not on this target)</option>
                )}
              </select>
            )}
            <button
              className="btn btn-accent px-2.5 py-1 text-xs"
              onClick={doRun}
              disabled={dry.isPending || !!review}
              title="Dry run (⌘↵): run this script against the target once. Nothing is recorded."
            >
              {dry.isPending ? <Loader2 size="13" className="animate-spin" /> : <Play size="13" />} Dry run
            </button>
            <div className="ml-auto flex items-center gap-2 text-[11px] text-muted">
              <a
                href={GUIDE_WRITING_TESTS}
                target="_blank"
                rel="noopener"
                className="flex items-center gap-1 hover:text-white"
                title="How test scripts work: check, expect_deny, record, sql, http… (opens in a new tab)"
              >
                <CircleHelp size="12" /> Help
              </a>
              {analysis && (
                <>
                  <span>{analysis.checks.length} {analysis.checks.length === 1 ? 'check' : 'checks'}</span>
                  <span className={tierClass(analysis.max_risk_tier)}>max {analysis.max_risk_tier}</span>
                  {errorCount > 0 ? (
                    <button className="text-fail hover:underline" onClick={() => { setTab('problems'); setCollapsed(false) }}>
                      {errorCount} {errorCount === 1 ? 'error' : 'errors'}
                    </button>
                  ) : (
                    <span className="text-pass">valid</span>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Editor (and the diff review over it) */}
          <div className="relative min-h-0 flex-1">
            <div className={review ? 'hidden' : 'h-full'}>
              <CodeEditor
                ref={editor}
                value={buffer}
                onChange={setBuffer}
                diagnostics={diagnostics}
                sdk={sdk}
                onSave={doSave}
                onRun={doRun}
              />
            </div>
            {review && (
              <div className="flex h-full flex-col">
                <div className="flex shrink-0 items-center gap-2 border-b border-accent/40 bg-accent/10 px-3 py-1.5 text-xs">
                  <FileDiff size="14" className="shrink-0 text-accent" />
                  <span className="shrink-0 font-medium text-white">Proposed edit</span>
                  <span className="truncate text-gray-300">{review.proposal.summary}</span>
                  {review.total > 0 && (
                    <span className="ml-auto shrink-0 text-muted">
                      {review.remaining} of {review.total} {review.total === 1 ? 'change' : 'changes'} left
                    </span>
                  )}
                  <button
                    className={`btn shrink-0 px-2 py-0.5 text-xs ${review.total > 0 ? '' : 'ml-auto'}`}
                    onClick={() => finishReview(review.original)}
                    title="Undo every change still highlighted; changes you've kept stay"
                  >
                    {review.remaining < review.total ? 'Undo the rest' : 'Undo all'}
                  </button>
                  <button
                    className="btn btn-accent shrink-0 px-2 py-0.5 text-xs"
                    onClick={() => finishReview(review.doc)}
                    title="Keep every change still highlighted"
                  >
                    {review.remaining < review.total ? 'Keep the rest' : 'Keep all'}
                  </button>
                </div>
                <div className="min-h-0 flex-1">
                  <CodeEditor
                    value={review.doc}
                    original={buffer}
                    onReviewChange={onReviewChange}
                    sdk={sdk}
                    diagnostics={review.proposal.analysis.errors.map((d) => ({ ...d, severity: 'error' as const }))}
                  />
                </div>
              </div>
            )}
          </div>

          <ResultsPanel
            tab={tab}
            setTab={setTab}
            analysis={analysis}
            dryRun={dryRun ? { ...dryRun, stale: dryRun.script !== buffer } : null}
            running={dry.isPending}
            runError={runError}
            onGoTo={(l) => {
              setReview(null)
              editor.current?.goToLine(l)
            }}
            collapsed={collapsed}
            setCollapsed={setCollapsed}
          />
        </div>

        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize editor and assistant"
          onMouseDown={split.startResize}
          onDoubleClick={split.reset}
          className="group flex w-1.5 shrink-0 cursor-col-resize items-center justify-center border-x border-edge bg-ink hover:bg-accent/30"
        >
          <GripVertical size="12" className="text-muted opacity-0 transition group-hover:opacity-100" />
        </div>

        <div className="min-w-0 flex-1 bg-ink">
          <EditorAssistant
            testId={testId}
            entries={entries}
            setEntries={setEntries}
            buffer={buffer}
            targetId={targetId}
            lastDryRun={dryRun}
            reviewing={!!review}
            onProposal={onProposal}
            onDryRun={onAgentDryRun}
            initialAsk={initialAsk.current}
            onInitialAskSent={() => {
              initialAsk.current = null
              setSearch((prev) => {
                const next = new URLSearchParams(prev)
                next.delete('ask')
                return next
              }, { replace: true })
            }}
          />
        </div>
      </div>
    </div>
  )
}

function EditorLoading() {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-muted">
      <Loader2 size="16" className="animate-spin" /> Loading the editor…
    </div>
  )
}

function EditorLoadError({ what, error, onRetry, onBlank }: {
  what: string
  error: unknown
  onRetry: () => void
  onBlank?: () => void
}) {
  const status = error instanceof ApiError ? error.status : null
  return (
    <div className="card m-6 max-w-xl text-sm">
      <div className="flex items-center gap-2 font-semibold text-white">
        <AlertCircle size="16" className="text-fail" /> Couldn't load {what}
      </div>
      <p className="mt-2 text-muted">
        {status ? `The server answered ${status}: ` : ''}
        <span className="text-gray-200">{errorMessage(error)}</span>
      </p>
      <p className="mt-2 text-muted">
        Try again in a moment.{onBlank ? ' You can also start from an empty script and write the test yourself.' : ''}{' '}
        If it keeps failing, the backend log has the details.
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="btn" onClick={onRetry}><RotateCw size="14" /> Try again</button>
        {onBlank && <button className="btn" onClick={onBlank}>Start from an empty script</button>}
        <Link className="btn" to="/tests"><ArrowLeft size="14" /> Back to tests</Link>
      </div>
    </div>
  )
}
