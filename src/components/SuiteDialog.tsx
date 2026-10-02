import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, Layers, Search, Trash2, X } from 'lucide-react'

import { useCreateSuite, useDeleteSuite, useUpdateSuite } from '../hooks/queries'
import { errorMessage } from '../lib/api'
import type { StudioTest, Suite } from '../types'

// Create or edit a Suite by hand: a name and an ordered pick of Tests.
export default function SuiteDialog({
  suite, tests, onClose, onSaved,
}: {
  suite: Suite | null
  tests: StudioTest[]
  onClose: () => void
  onSaved: (id: string | null) => void
}) {
  const create = useCreateSuite()
  const update = useUpdateSuite()
  const remove = useDeleteSuite()
  const [name, setName] = useState(suite?.name ?? '')
  const [description, setDescription] = useState(suite?.description ?? '')
  const [enabled, setEnabled] = useState(suite?.enabled ?? true)
  const [picked, setPicked] = useState<string[]>(suite?.test_ids ?? [])
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const byId = useMemo(() => new Map(tests.map((t) => [t.id, t])), [tests])
  const available = tests.filter(
    (t) => !picked.includes(t.id) && (!q.trim() || `${t.name} ${t.tags.join(' ')}`.toLowerCase().includes(q.trim().toLowerCase())),
  )

  const move = (i: number, d: -1 | 1) =>
    setPicked((p) => {
      const next = [...p]
      const j = i + d
      if (j < 0 || j >= next.length) return p
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })

  async function submit() {
    if (!name.trim()) return setError('Give the suite a name.')
    if (!picked.length) return setError('Add at least one test. A suite with no tests has nothing to run.')
    setError(null)
    const body = { name: name.trim(), description, test_ids: picked, enabled }
    try {
      const out = suite ? await update.mutateAsync({ id: suite.id, ...body }) : await create.mutateAsync(body)
      onSaved(out.id)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  async function destroy() {
    if (!suite || !window.confirm(`Delete the suite "${suite.name}"? Its tests are kept.`)) return
    try {
      await remove.mutateAsync(suite.id)
      onSaved(null)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const busy = create.isPending || update.isPending || remove.isPending

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" role="dialog" aria-modal="true"
         onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card flex max-h-[85vh] w-full max-w-3xl flex-col">
        <div className="mb-3 flex items-center gap-2">
          <Layers size="16" className="text-accent" />
          <div className="text-sm font-semibold text-white">{suite ? `Edit ${suite.name}` : 'New suite'}</div>
          <button className="ml-auto text-muted hover:text-white" onClick={onClose}><X size="16" /></button>
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_1fr]">
          <input className="input" placeholder="Suite name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          <input className="input" placeholder="Description (optional)" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        {suite && (
          <label className="mt-2 flex items-center gap-2 text-sm text-gray-200">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled
          </label>
        )}

        <div className="mt-3 grid min-h-0 flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex min-h-0 flex-col rounded-lg border border-edge">
            <div className="border-b border-edge p-2">
              <label className="relative block">
                <Search size="13" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
                <input className="input w-full py-1 pl-8 text-xs" placeholder="Find tests…" value={q} onChange={(e) => setQ(e.target.value)} />
              </label>
            </div>
            <ul className="min-h-[180px] flex-1 overflow-auto p-1">
              {available.map((t) => (
                <li key={t.id}>
                  <button
                    className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm text-gray-300 hover:bg-ink-soft hover:text-white"
                    onClick={() => setPicked((p) => [...p, t.id])}
                  >
                    <span className="truncate">{t.name}</span>
                    {t.status === 'draft' && <span className="badge bg-warn/15 text-warn">draft</span>}
                    <span className="ml-auto text-[11px] text-accent">add</span>
                  </button>
                </li>
              ))}
              {!available.length && <li className="px-2 py-1 text-xs text-muted">No more tests.</li>}
            </ul>
          </div>

          <div className="flex min-h-0 flex-col rounded-lg border border-edge">
            <div className="border-b border-edge px-3 py-2 card-title">In this suite ({picked.length}) — runs in this order</div>
            <ol className="min-h-[180px] flex-1 overflow-auto p-1">
              {picked.map((id, i) => {
                const t = byId.get(id)
                return (
                  <li key={id} className="flex items-center gap-1 rounded px-2 py-1 text-sm hover:bg-ink-soft">
                    <span className="w-5 text-[11px] text-muted">{i + 1}</span>
                    <span className="truncate text-gray-200">{t?.name ?? id}</span>
                    {t?.status === 'draft' && <span className="badge bg-warn/15 text-warn" title="Drafts don't run until activated">draft</span>}
                    <span className="ml-auto flex shrink-0 items-center">
                      <button className="p-0.5 text-muted hover:text-white disabled:opacity-30" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp size="12" /></button>
                      <button className="p-0.5 text-muted hover:text-white disabled:opacity-30" disabled={i === picked.length - 1} onClick={() => move(i, 1)}><ArrowDown size="12" /></button>
                      <button className="p-0.5 text-muted hover:text-fail" onClick={() => setPicked((p) => p.filter((x) => x !== id))}><X size="12" /></button>
                    </span>
                  </li>
                )
              })}
              {!picked.length && <li className="px-2 py-1 text-xs text-muted">Add tests from the left.</li>}
            </ol>
          </div>
        </div>

        {error && <div className="mt-2 text-xs text-fail">{error}</div>}

        <div className="mt-3 flex items-center gap-2">
          {suite && (
            <button className="btn text-muted hover:text-fail" onClick={destroy} disabled={busy}>
              <Trash2 size="14" /> Delete suite
            </button>
          )}
          {!picked.length && <span className="ml-auto text-xs text-muted">Add at least one test to save.</span>}
          <button className={`btn ${picked.length ? 'ml-auto' : ''}`} onClick={onClose}>Cancel</button>
          <button
            className="btn btn-accent"
            onClick={submit}
            disabled={busy || !picked.length}
            title={picked.length ? undefined : 'A suite needs at least one test'}
          >
            {suite ? 'Save suite' : 'Create suite'}
          </button>
        </div>
      </div>
    </div>
  )
}
