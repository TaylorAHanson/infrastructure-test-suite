// Applies the branding record to the app shell (ADR-0013 §4): the theme's closed
// named-token palette as CSS custom properties (light/dark-aware), the document
// title, and the favicon. The shell reads branding on load and the poll refreshes
// it, so calling this from an effect keyed on the record makes a branding edit
// instant-apply within the poll interval — no rebuild, no push infra.
import { useEffect, useState } from 'react'

import type { Branding, BrandTheme } from '../types'

/** "#rrggbb" / "#rgb" → the "r g b" channel triplet Tailwind's colour vars use. */
export function hexToChannels(hex: string): string | null {
  const m = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.exec(hex?.trim() ?? '')
  if (!m) return null
  let h = m[1]
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  const n = parseInt(h, 16)
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`
}

function prefersDark(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-color-scheme: dark)').matches
}

const ON_ACCENT_DARK = '11 18 32'
const ON_ACCENT_LIGHT = '255 255 255'

function luminance(channels: string): number {
  const [r, g, b] = channels.split(' ').map((c) => {
    const v = Number(c) / 255
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** Dark or white text, whichever reads better on the accent (WCAG contrast). */
export function onAccentChannels(accent: string): string {
  const l = luminance(accent)
  const vsDark = (l + 0.05) / (luminance(ON_ACCENT_DARK) + 0.05)
  const vsLight = 1.05 / (l + 0.05)
  return vsDark >= vsLight ? ON_ACCENT_DARK : ON_ACCENT_LIGHT
}

/** Paint the theme onto :root as `--brand-*` channel vars for the active scheme. */
export function applyTheme(theme: BrandTheme | undefined): void {
  if (!theme || typeof document === 'undefined') return
  const root = document.documentElement
  const scheme = prefersDark() ? 'dark' : 'light'

  const accent = hexToChannels(theme.accent)
  if (accent) {
    root.style.setProperty('--brand-accent', accent)
    root.style.setProperty('--on-accent', onAccentChannels(accent))
  }

  for (const [name, value] of Object.entries(theme.tokens ?? {})) {
    const channels = hexToChannels(value?.[scheme] ?? value?.dark)
    if (channels) root.style.setProperty(`--brand-${name}`, channels)
  }
}

function applyFavicon(branding: Branding): void {
  if (typeof document === 'undefined') return
  const href = branding.has_favicon ? `/api/v1/branding/favicon?v=${branding.updated_at}` : null
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (!href) return // keep the bundled default when none is set
  if (!link) {
    link = document.createElement('link')
    link.rel = 'icon'
    document.head.appendChild(link)
  }
  link.href = href
  if (branding.favicon_content_type) link.type = branding.favicon_content_type
}

/** Apply the full branding record to the document (title + favicon + theme). */
export function applyBranding(branding: Branding): void {
  if (typeof document !== 'undefined' && branding.app_name) {
    document.title = branding.app_name
  }
  applyTheme(branding.theme)
  applyFavicon(branding)
}

/** The OS colour scheme, kept current as it flips. */
export function useColorScheme(): 'dark' | 'light' {
  const [scheme, setScheme] = useState<'dark' | 'light'>(() => (prefersDark() ? 'dark' : 'light'))
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!mq) return
    const onChange = () => setScheme(mq.matches ? 'dark' : 'light')
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])
  return scheme
}

/** Effect hook: (re)apply branding whenever the record changes, and re-apply the
 *  theme when the OS colour scheme flips so it stays light/dark-aware. */
export function useApplyBranding(branding: Branding | undefined): void {
  const key = branding
    ? JSON.stringify([branding.app_name, branding.theme, branding.has_favicon, branding.updated_at])
    : null

  useEffect(() => {
    if (!branding) return
    applyBranding(branding)
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!mq) return
    const onChange = () => applyTheme(branding.theme)
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
}
