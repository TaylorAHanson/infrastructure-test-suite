// The Assistant page-context contract (ADR-0012 §2). Each page *declares* what
// it's showing (the Test being edited, the target list, the finding under
// triage); the app-wide Assistant shelf reads it so its help is grounded in
// what the user is looking at. This is the wire that makes one app-wide shelf
// page-aware without embedding it in any single view.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type { PageContext } from '../types'

// A request to open the shelf pre-seeded with input text. The `nonce` makes each
// request distinct even when the text repeats, so the shelf re-opens/re-seeds on
// every call (e.g. clicking "New test" twice) rather than only on a text change.
export interface SeedPrompt {
  text: string
  nonce: number
  /** Which Assistant capability the seeded conversation targets (e.g.
   *  `authoring`). Omitted = keep the shelf's current mode. */
  mode?: AssistantMode
}

/** The turn `mode` the harness accepts; `auto` picks from the page context. */
export type AssistantMode = 'auto' | 'authoring'

interface Ctx {
  pageContext: PageContext
  setPageContext: (ctx: PageContext) => void
  // The general entry-point mechanism: any surface can open the Assistant shelf
  // pre-seeded with a starting sentence. The shelf watches `seedPrompt`; callers
  // invoke `openWithPrompt` to raise a fresh request.
  seedPrompt: SeedPrompt | null
  openWithPrompt: (text: string, mode?: AssistantMode) => void
}

const AssistantContext = createContext<Ctx | null>(null)

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [pageContext, setPageContext] = useState<PageContext>({ page: '' })
  const [seedPrompt, setSeedPrompt] = useState<SeedPrompt | null>(null)
  const openWithPrompt = useCallback((text: string, mode?: AssistantMode) => {
    setSeedPrompt((prev) => ({ text, nonce: (prev?.nonce ?? 0) + 1, mode }))
  }, [])
  const value = useMemo(
    () => ({ pageContext, setPageContext, seedPrompt, openWithPrompt }),
    [pageContext, seedPrompt, openWithPrompt],
  )
  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>
}

export function useAssistantContext(): Ctx {
  const ctx = useContext(AssistantContext)
  if (!ctx) throw new Error('useAssistantContext must be used within AssistantProvider')
  return ctx
}

// Pages call this to declare their page context (ADR-0012). It is applied while
// the page is mounted and reset to a bare page name on unmount.
export function useDeclarePageContext(ctx: PageContext) {
  const { setPageContext } = useAssistantContext()
  const key = JSON.stringify(ctx)
  useEffect(() => {
    setPageContext(ctx)
    return () => setPageContext({ page: ctx.page })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setPageContext])
}
