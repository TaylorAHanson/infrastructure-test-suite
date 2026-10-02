import { useEffect, useRef, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { useBranding, useUpdateBranding } from '../hooks/queries'
import type { BrandingUpdateBody } from '../hooks/queries'

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

// Branding editor (ADR-0013 / #38): any authenticated human may edit the global
// branding record — no friction ceremony (presentation can't defeat a safety
// gate) but every save is audited. Edits are instant-apply via the shell poll.
export default function Branding() {
  const { data: branding } = useBranding()
  const update = useUpdateBranding()
  const logoInput = useRef<HTMLInputElement>(null)
  const faviconInput = useRef<HTMLInputElement>(null)

  const [appName, setAppName] = useState('')
  const [assistantName, setAssistantName] = useState('')
  const [footer, setFooter] = useState('')
  const [accent, setAccent] = useState('#ff6b3d')
  const [pendingLogo, setPendingLogo] = useState<string | null | undefined>(undefined)
  const [pendingFavicon, setPendingFavicon] = useState<string | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!branding) return
    setAppName(branding.app_name)
    setAssistantName(branding.assistant_name)
    setFooter(branding.footer_text)
    setAccent(branding.theme?.accent ?? '#ff6b3d')
  }, [branding])

  const onSave = () => {
    setError(null)
    const body: BrandingUpdateBody = {
      app_name: appName,
      assistant_name: assistantName,
      footer_text: footer,
      theme: { accent, tokens: branding?.theme?.tokens ?? {} },
    }
    if (pendingLogo === null) body.clear_logo = true
    else if (typeof pendingLogo === 'string') body.logo = pendingLogo
    if (pendingFavicon === null) body.clear_favicon = true
    else if (typeof pendingFavicon === 'string') body.favicon = pendingFavicon
    update.mutate(body, {
      onSuccess: () => {
        setPendingLogo(undefined)
        setPendingFavicon(undefined)
      },
      onError: (e: unknown) => setError(e instanceof Error ? e.message : 'update failed'),
    })
  }

  const onPickImage = async (
    e: React.ChangeEvent<HTMLInputElement>,
    set: (v: string | null | undefined) => void,
  ) => {
    const file = e.target.files?.[0]
    if (file) set(await readAsDataUrl(file))
  }

  const hasLogo = pendingLogo === undefined ? branding?.has_logo : pendingLogo !== null
  const hasFavicon = pendingFavicon === undefined ? branding?.has_favicon : pendingFavicon !== null

  return (
    <div>
      <PageHeader
        title="Branding"
        subtitle="App name, Assistant name, logo, favicon, accent colour and footer. Changes apply within seconds and are audited."
      />
      <div className="card">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="text-xs text-muted">
            App name
            <input className="input mt-1 w-full" value={appName} onChange={(e) => setAppName(e.target.value)} />
          </label>
          <label className="text-xs text-muted">
            Assistant display name
            <input className="input mt-1 w-full" value={assistantName} onChange={(e) => setAssistantName(e.target.value)} />
          </label>
          <label className="text-xs text-muted md:col-span-2">
            Footer / attribution
            <input className="input mt-1 w-full" value={footer} onChange={(e) => setFooter(e.target.value)} />
          </label>
          <label className="flex items-center gap-2 text-xs text-muted">
            Accent colour
            <input type="color" value={accent} onChange={(e) => setAccent(e.target.value)} className="h-8 w-10 rounded border border-edge bg-ink" />
            <span className="font-mono text-white">{accent}</span>
          </label>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span>Logo {hasLogo ? '(set)' : '(wordmark)'}</span>
            <input ref={logoInput} type="file" accept="image/png,image/svg+xml" className="hidden" onChange={(e) => onPickImage(e, setPendingLogo)} />
            <button type="button" className="btn py-1" onClick={() => logoInput.current?.click()}>Upload</button>
            {hasLogo && <button type="button" className="btn py-1" onClick={() => setPendingLogo(null)}>Clear</button>}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span>Favicon {hasFavicon ? '(set)' : '(default)'}</span>
            <input ref={faviconInput} type="file" accept="image/png,image/svg+xml" className="hidden" onChange={(e) => onPickImage(e, setPendingFavicon)} />
            <button type="button" className="btn py-1" onClick={() => faviconInput.current?.click()}>Upload</button>
            {hasFavicon && <button type="button" className="btn py-1" onClick={() => setPendingFavicon(null)}>Clear</button>}
          </div>
        </div>
        {error && <div className="mt-3 text-xs text-fail">{error}</div>}
        <div className="mt-3 flex items-center gap-3">
          <button type="button" className="btn btn-accent" onClick={onSave} disabled={update.isPending}>
            {update.isPending ? 'Saving…' : 'Save branding'}
          </button>
          <span className="text-[11px] text-muted">PNG or SVG, ≤ 1 MB. Theme is a closed token palette — no free-form CSS.</span>
        </div>
      </div>
    </div>
  )
}
