import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

import { Badge } from '../components/Badge'
import { PageHeader } from '../components/PageHeader'
import { useChecks, useTests } from '../hooks/queries'
import { statusClass } from '../lib/format'
import type { Check, Operation, StudioTest } from '../types'

function opLabel(op?: Operation): string {
  if (!op) return '-'
  const call = [op.service, op.method].filter(Boolean).join('.')
  return op.verb ? `${op.verb.toUpperCase()} ${call || '(sdk)'}` : call || '(sdk)'
}

export default function TestCatalog() {
  const { data: tests } = useTests()
  const { data: checks } = useChecks()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')

  const checkById = useMemo(() => {
    const m = new Map<string, Check>()
    for (const c of checks ?? []) m.set(c.id, c)
    return m
  }, [checks])

  const filtered = useMemo(() => {
    return (tests ?? []).filter((t) => {
      const matchesQ =
        !q ||
        t.name.toLowerCase().includes(q.toLowerCase()) ||
        t.tags.some((tag) => tag.toLowerCase().includes(q.toLowerCase()))
      const matchesStatus = !status || t.status === status
      return matchesQ && matchesStatus
    })
  }, [tests, q, status])

  return (
    <div>
      <PageHeader
        title="Test Catalog"
        subtitle="Tests are target-agnostic, ordered bundles of Checks (incl. counterfactuals). A Check is one open-SDK operation + an expectation + an evaluator."
      />
      <div className="card mb-4 flex flex-wrap gap-3">
        <input
          className="input flex-1"
          placeholder="Search by name or tag..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="draft">Draft</option>
        </select>
      </div>

      <div className="space-y-2">
        {filtered.map((t) => (
          <TestRow key={t.id} test={t} checkById={checkById} />
        ))}
        {tests && !filtered.length && (
          <div className="card text-sm text-muted">No Tests match this search.</div>
        )}
      </div>
    </div>
  )
}

function TestRow({ test, checkById }: { test: StudioTest; checkById: Map<string, Check> }) {
  const [open, setOpen] = useState(false)
  const members = [...test.members].sort((a, b) => a.sort_order - b.sort_order)
  const counterfactuals = members.filter(
    (m) => m.kind === 'counterfactual' || checkById.get(m.check_id)?.expectation === 'DENY',
  ).length

  return (
    <div className="card">
      <button className="flex w-full items-center justify-between text-left" onClick={() => setOpen((o) => !o)}>
        <div className="flex items-center gap-3">
          {open ? <ChevronDown size="14" /> : <ChevronRight size="14" />}
          <span className="text-base font-semibold text-white">{test.name}</span>
          <Badge value={test.status} />
          <span className="badge bg-ink-soft text-muted">v{test.version}</span>
          <span className="badge bg-ink-soft text-muted">{members.length} checks</span>
          {counterfactuals > 0 && (
            <span className="badge bg-info/15 text-info">{counterfactuals} counterfactual</span>
          )}
        </div>
        <span className="text-xs text-muted">{test.origin}</span>
      </button>

      {test.description && <p className="mt-1 pl-6 text-sm text-muted">{test.description}</p>}
      {test.tags.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1 pl-6">
          {test.tags.map((tag) => (
            <span key={tag} className="badge bg-ink-soft text-muted">{tag}</span>
          ))}
        </div>
      )}

      {open && (
        <div className="mt-3 overflow-hidden rounded-lg border border-edge">
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
                return (
                  <tr key={m.check_id} className="border-t border-edge/50">
                    <td className="td text-xs text-muted">{i + 1}</td>
                    <td className="td">
                      <div className="text-sm text-white">{check?.display_name ?? m.check_id}</div>
                      {check?.assertion && <div className="text-xs text-muted">{check.assertion}</div>}
                    </td>
                    <td className="td font-mono text-xs text-info">{opLabel(check?.operation)}</td>
                    <td className="td">
                      {check && <span className={`badge ${statusClass(check.evaluator)}`}>{check.evaluator}</span>}
                    </td>
                    <td className="td">
                      {isCf ? (
                        <span className="badge bg-info/15 text-info">
                          counterfactual{check?.expected_stage ? ` @ ${check.expected_stage}` : ''}
                        </span>
                      ) : (
                        <span className="text-xs text-muted">assertion</span>
                      )}
                    </td>
                    <td className="td text-xs text-muted">{check?.risk_tier ?? '-'}</td>
                  </tr>
                )
              })}
              {!members.length && (
                <tr><td className="td text-xs text-muted" colSpan={6}>This Test has no Checks.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
