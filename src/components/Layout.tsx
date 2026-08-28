import { NavLink, Outlet } from 'react-router-dom'
import {
  Activity,
  CalendarClock,
  ClipboardList,
  Database,
  FlaskConical,
  Gauge,
  Layers,
  ListChecks,
  Settings,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'

import { useAdminSettings, useBranding } from '../hooks/queries'
import { AssistantProvider } from '../lib/assistantContext'
import { useApplyBranding } from '../lib/branding'
import AssistantShelf from './AssistantShelf'

const NAV = [
  { to: '/', label: 'Dashboard', icon: Gauge, end: true },
  { to: '/runs', label: 'Runs', icon: ListChecks },
  { to: '/tests', label: 'Test Catalog', icon: FlaskConical },
  { to: '/studio', label: 'Test Studio', icon: Sparkles },
  { to: '/suites', label: 'Suites', icon: Layers },
  { to: '/targets', label: 'Targets & Catalogs', icon: Database },
  { to: '/schedules', label: 'Schedules', icon: CalendarClock },
  { to: '/analytics', label: 'Failure Analytics', icon: Activity },
  { to: '/audit', label: 'Audit Log', icon: ClipboardList },
  { to: '/admin', label: 'Admin & Settings', icon: Settings },
]

export default function Layout() {
  const { data: admin } = useAdminSettings()
  const { data: branding } = useBranding()
  // Instant-apply: title, favicon, and theme follow the branding record + poll.
  useApplyBranding(branding)
  const flags = (admin?.feature_flags ?? {}) as Record<string, boolean>
  const demo = flags.demo_mode
  const appName = branding?.app_name ?? 'Validation Platform'
  const footer = branding?.footer_text

  return (
    <div className="flex h-full">
      <aside className="flex w-64 shrink-0 flex-col border-r border-edge bg-ink p-4">
        <div className="mb-6 flex items-center gap-2 px-2">
          {branding?.has_logo ? (
            <img
              src={`/api/v1/branding/logo?v=${branding.updated_at}`}
              alt={appName}
              className="h-6 w-auto max-w-[160px] object-contain"
            />
          ) : (
            <>
              {/* Text wordmark when no logo is set (ADR-0013 §5). */}
              <ShieldAlert className="text-accent" size="22" />
              <div>
                <div className="text-sm font-semibold text-white">{appName}</div>
                <div className="text-[11px] text-muted">Databricks connectivity platform</div>
              </div>
            </>
          )}
        </div>
        <nav className="flex flex-1 flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
            >
              <Icon size="16" />
              {label}
            </NavLink>
          ))}
        </nav>
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
      </aside>
      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-[1400px] p-6">
          <AssistantProvider>
            <Outlet />
            {/* The one app-wide Assistant, reachable from every page (ADR-0012). */}
            <AssistantShelf />
          </AssistantProvider>
        </div>
      </main>
    </div>
  )
}
