// The Assistant page-context contract (ADR-0012 §2). Each page *declares* what
// it's showing (the Test being edited, the target list, the finding under
// triage); the app-wide Assistant shelf reads it so its help is grounded in
// what the user is looking at. This is the wire that makes one app-wide shelf
// page-aware without embedding it in any single view.
import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import type { PageContext } from '../types'

interface Ctx {
  pageContext: PageContext
  setPageContext: (ctx: PageContext) => void
}

const AssistantContext = createContext<Ctx | null>(null)

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [pageContext, setPageContext] = useState<PageContext>({ page: '' })
  const value = useMemo(() => ({ pageContext, setPageContext }), [pageContext])
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
