import { useEffect, useRef, useState } from 'react'

import { PageHeader } from '../components/PageHeader'
import { useAdminSettings, useBranding, useUpdateBranding } from '../hooks/queries'
import type { BrandingUpdateBody } from '../hooks/queries'

// Remote agents were dropped as out of scope, so this page no longer calls the
// removed /agents endpoint. It renders only what /admin/settings actually
// returns: the registered handlers, the canonical stage spine, the (extensible)
// failure taxonomy, and catalog counts.
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
  catalog_counts?: Record<string, number>
}

const COUNT_LABEL: Record<string, string> = {
  checks: 'Checks',
  tests: 'Tests',
  suites: 'Suites',
  targets: 'Targets',
  check_runs: 'CheckRuns',
}

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
function BrandingSettings() {
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
    <div className="card mb-4">
      <div className="card-title mb-1">Branding</div>
      <p className="mb-3 text-xs text-muted">
        Runtime white-label — app name, Assistant name, footer, accent, logo &amp; favicon. Any
        authenticated user can edit; every change is audited and applies within the poll interval.
      </p>
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
  )
}

export default function Admin() {
  const { data } = useAdminSettings()
  const s = (data ?? {}) as AdminSettings
  const counts = s.catalog_counts ?? {}

  return (
    <div>
      <PageHeader title="Admin & Settings" subtitle="Branding, registered handlers, the canonical stage spine, the failure taxonomy, and catalog counts" />

      <BrandingSettings />

      <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-5">
        {Object.entries(COUNT_LABEL).map(([key, label]) => (
          <div key={key} className="card py-3">
            <div className="card-title">{label}</div>
            <div className="stat">{counts[key] ?? '-'}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <div className="card-title mb-2">Registered handlers</div>
          <div className="flex flex-wrap gap-2">
            {(s.registered_handler_keys ?? []).map((k) => (
              <span key={k} className="badge bg-ink-soft text-info">{k}</span>
            ))}
            {!s.registered_handler_keys?.length && <span className="text-sm text-muted">none</span>}
          </div>
        </div>

        <div className="card">
          <div className="card-title mb-2">Canonical stage spine</div>
          <ol className="flex flex-wrap items-center gap-1 text-xs">
            {(s.canonical_stages ?? []).map((st, i) => (
              <li key={st.id} className="flex items-center gap-1">
                <span className={`badge ${st.connectivity ? 'bg-info/15 text-info' : 'bg-warn/15 text-warn'}`}>
                  {st.label ?? st.id}
                </span>
                {i < (s.canonical_stages?.length ?? 0) - 1 && <span className="text-muted">→</span>}
              </li>
            ))}
          </ol>
          <p className="mt-2 text-xs text-muted">
            Blue = connectivity stage (failure here ≤ AUTHORIZE); amber = functional (OPERATE / VERIFY).
          </p>
        </div>

        <div className="card lg:col-span-2">
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
                      <span className={`badge ${b.connectivity ? 'bg-info/15 text-info' : 'bg-warn/15 text-warn'}`}>
                        {b.connectivity ? 'connectivity' : 'functional'}
                      </span>
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
    </div>
  )
}
