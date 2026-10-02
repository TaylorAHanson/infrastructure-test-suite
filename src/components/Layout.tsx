import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Bell,
  BookOpen,
  CalendarClock,
  ChevronRight,
  ClipboardList,
  Compass,
  Database,
  FlaskConical,
  ListChecks,
  Palette,
  ScrollText,
  Settings,
  ShieldAlert,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { useAdminSettings, useBranding } from '../hooks/queries'
import { AssistantProvider } from '../lib/assistantContext'
import { useApplyBranding } from '../lib/branding'
import { useShelfWidth } from '../hooks/useShelfWidth'
import AssistantShelf from './AssistantShelf'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

const NAV: NavItem[] = [
  { to: '/', label: "What's Interesting", icon: Compass, end: true },
  { to: '/runs', label: 'Runs', icon: ListChecks },
  { to: '/tests', label: 'Tests', icon: FlaskConical },
  { to: '/audit', label: 'Audit Log', icon: ClipboardList },
]

// Configuration the day-to-day flow rarely needs, tucked into one collapsible
// group (collapsed by default; it opens itself while you're on a settings page).
const SETTINGS_NAV: NavItem[] = [
  { to: '/settings/branding', label: 'Branding', icon: Palette },
  { to: '/settings/targets', label: 'Targets', icon: Database },
  { to: '/settings/schedules', label: 'Schedules', icon: CalendarClock },
  { to: '/settings/notifications', label: 'Notifications', icon: Bell },
  { to: '/settings/release-notes', label: 'Release notes', icon: ScrollText },
]

function NavItemLink({ item, rail }: { item: NavItem; rail: boolean }) {
  const { to, label, icon: Icon, end } = item
  return (
    <NavLink
      to={to}
      end={end}
      title={rail ? label : undefined}
      className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''} ${rail ? 'justify-center' : ''}`}
    >
      <Icon size="16" />
      {!rail && label}
    </NavLink>
  )
}

export default function Layout() {
  const { data: admin } = useAdminSettings()
  const { data: branding } = useBranding()
  // Instant-apply: title, favicon, and theme follow the branding record + poll.
  useApplyBranding(branding)
  const flags = (admin?.feature_flags ?? {}) as Record<string, boolean>
  const demo = flags.demo_mode
  const appName = branding?.app_name ?? 'Validation Platform'
  const footer = branding?.footer_text

  // The Assistant shelf is a resizable push-pane sibling of <main> (#58). When
  // it grows to the half-viewport threshold the nav can't sit comfortably beside
  // it, so the sidebar auto-collapses to an icon rail — derived, never toggled.
  const [shelfOpen, setShelfOpen] = useState(false)
  const { width, collapsed, startResize } = useShelfWidth()
  const { pathname } = useLocation()
  // The test editor brings its own assistant and wants the whole width.
  const inEditor = /^\/tests\/(new|[^/]+\/edit)\/?$/.test(pathname)
  const navCollapsed = inEditor || (shelfOpen && collapsed)

  const inSettings = pathname.startsWith('/settings')
  const [settingsOpen, setSettingsOpen] = useState(inSettings)
  useEffect(() => {
    if (inSettings) setSettingsOpen(true)
  }, [inSettings])

  return (
    <AssistantProvider>
      <div className="flex h-full">
        <aside
          className={`flex shrink-0 flex-col border-r border-edge bg-ink py-4 transition-[width] ${
            navCollapsed ? 'w-16 px-2' : 'w-64 px-4'
          }`}
        >
          <div className={`mb-6 flex items-center gap-2 ${navCollapsed ? 'justify-center' : 'px-2'}`}>
            {branding?.has_logo ? (
              <img
                src={`/api/v1/branding/logo?v=${branding.updated_at}`}
                alt={appName}
                className={`object-contain ${navCollapsed ? 'h-6 w-6' : 'h-6 w-auto max-w-[160px]'}`}
              />
            ) : (
              <>
                {/* Text wordmark when no logo is set (ADR-0013 §5). */}
                <ShieldAlert className="text-accent" size="22" />
                {!navCollapsed && (
                  <div>
                    <div className="text-sm font-semibold text-white">{appName}</div>
                    <div className="text-[11px] text-muted">Databricks connectivity platform</div>
                  </div>
                )}
              </>
            )}
          </div>
          <nav className="flex flex-1 flex-col gap-1">
            {NAV.map((item) => (
              <NavItemLink key={item.to} item={item} rail={navCollapsed} />
            ))}

            <div className="mt-auto border-t border-edge pt-3">
              {/* The operator guide is a separate static site served at /guide/ (not
                  a client route), so it's a plain link. It opens in a new tab so an
                  in-progress Assistant conversation or run isn't lost. */}
              <a
                href="/guide/"
                target="_blank"
                rel="noopener"
                title={navCollapsed ? 'Docs' : 'Opens in a new tab'}
                className={`nav-link mb-1 ${navCollapsed ? 'justify-center' : ''}`}
              >
                <BookOpen size="16" />
                {!navCollapsed && 'Docs'}
              </a>
              <button
                type="button"
                onClick={() => setSettingsOpen((o) => !o)}
                aria-expanded={settingsOpen}
                title={navCollapsed ? 'Settings' : undefined}
                className={`nav-link w-full ${inSettings && !settingsOpen ? 'nav-link-active' : ''} ${
                  navCollapsed ? 'justify-center' : ''
                }`}
              >
                <Settings size="16" />
                {!navCollapsed && (
                  <>
                    Settings
                    <ChevronRight size="14" className={`ml-auto transition-transform ${settingsOpen ? 'rotate-90' : ''}`} />
                  </>
                )}
              </button>
              {settingsOpen && (
                <div className={`mt-1 flex flex-col gap-1 ${navCollapsed ? '' : 'ml-4 border-l border-edge pl-2'}`}>
                  {SETTINGS_NAV.map((item) => (
                    <NavItemLink key={item.to} item={item} rail={navCollapsed} />
                  ))}
                </div>
              )}
            </div>
          </nav>
          {!navCollapsed && (
            <div className="mt-4 rounded-lg border border-edge bg-panel p-3 text-[11px] text-muted">
              <div className="flex items-center justify-between">
                <span>Environment</span>
                <span className="text-white">{(admin?.environment as string) ?? '...'}</span>
              </div>
              <div className="mt-1 flex items-center justify-between">
                <span>Mode</span>
                <span className={demo ? 'text-warn' : 'text-pass'}>{demo ? 'Demo / mock' : 'Live'}</span>
              </div>
              {footer && <div className="mt-2 border-t border-edge pt-2 text-muted">{footer}</div>}
            </div>
          )}
        </aside>
        {inEditor ? (
          <main className="min-w-0 flex-1 overflow-hidden p-4">
            <Outlet />
          </main>
        ) : (
          <main className="min-w-0 flex-1 overflow-auto">
            <div className="mx-auto max-w-[1400px] p-6">
              <Outlet />
            </div>
          </main>
        )}
        {/* The one app-wide Assistant, reachable from every page (ADR-0012),
            sharing space with <main> as a resizable push-pane (#58). */}
        {!inEditor && (
          <AssistantShelf
            open={shelfOpen}
            onOpen={() => setShelfOpen(true)}
            onClose={() => setShelfOpen(false)}
            width={width}
            onResizeStart={startResize}
          />
        )}
      </div>
    </AssistantProvider>
  )
}
