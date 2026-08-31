import { useState } from 'react'
import { MessageSquarePlus } from 'lucide-react'

import { PageHeader } from '../components/PageHeader'
import { useAssistantContext, useDeclarePageContext } from '../lib/assistantContext'
import { useSuites, useTests } from '../hooks/queries'

export default function Suites() {
  const { data: suites } = useSuites()
  const { data: tests } = useTests()
  const { openWithPrompt } = useAssistantContext()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const nameOf = (id: string) => tests?.find((t) => t.id === id)?.name ?? id

  useDeclarePageContext({ page: 'suites', suite_id: selectedId ?? undefined })

  return (
    <div>
      <PageHeader
        title="Suites"
        subtitle="A Suite is a named selection of Tests for a Run. A deterministic-only Suite computes its fail-closed verdict from deterministic Checks alone (agentic Checks stay advisory)."
        actions={
          // Persistent entry point, shown whether or not the catalog is empty —
          // same treatment as Test Studio's button (issue #59/#66): the chat
          // icon + "Describe a…" label signal it opens a conversation, not a
          // blank create form (there is no manual Suite-creation form, ADR-0021).
          <button
            className="btn btn-accent"
            onClick={() => openWithPrompt('Create a new suite that ')}
          >
            <MessageSquarePlus size="15" /> Describe a new suite
          </button>
        }
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {suites?.map((s) => (
          <div
            key={s.id}
            className={`card cursor-pointer ${selectedId === s.id ? 'ring-2 ring-accent' : ''}`}
            onClick={() => setSelectedId(s.id)}
          >
            <div className="flex items-center justify-between">
              <div className="text-base font-semibold text-white">{s.name}</div>
              <div className="flex items-center gap-2">
                {s.deterministic_only && (
                  <span className="badge bg-pass/15 text-pass">deterministic-only</span>
                )}
                <span className="badge bg-ink-soft text-muted">{s.test_ids.length} tests</span>
              </div>
            </div>
            {s.description && <p className="mt-1 text-sm text-muted">{s.description}</p>}
            {s.tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {s.tags.map((tag) => (
                  <span key={tag} className="badge bg-ink-soft text-muted">{tag}</span>
                ))}
              </div>
            )}
            <ul className="mt-3 space-y-1">
              {s.test_ids.slice(0, 12).map((id) => (
                <li key={id} className="text-sm text-gray-200">· {nameOf(id)}</li>
              ))}
              {s.test_ids.length > 12 && (
                <li className="text-xs text-muted">+ {s.test_ids.length - 12} more</li>
              )}
              {!s.test_ids.length && <li className="text-sm text-muted">No Tests selected.</li>}
            </ul>
          </div>
        ))}
        {suites && !suites.length && (
          <div className="card text-sm text-muted">No Suites defined yet.</div>
        )}
      </div>
    </div>
  )
}
