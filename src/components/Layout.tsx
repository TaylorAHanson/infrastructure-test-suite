import { NavLink, Outlet } from 'react-router-dom'
import {
  CalendarClock,
  ClipboardList,
  Compass,
  Database,
  FlaskConical,
  Layers,
  ListChecks,
  Settings,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'

import { useAdminSettings } from '../hooks/queries'
import { AssistantProvider } from '../lib/assistantContext'
import AssistantShelf from './AssistantShelf'

const NAV = [
  { to: '/', label: "What's Interesting", icon: Compass, end: true },
  { to: '/runs', label: 'Runs', icon: ListChecks },
  { to: '/tests', label: 'Test Catalog', icon: FlaskConical },
  { to: '/studio', label: 'Test Studio', icon: Sparkles },
  { to: '/suites', label: 'Suites', icon: Layers },
  { to: '/targets', label: 'Targets & Catalogs', icon: Database },
  { to: '/schedules', label: 'Schedules', icon: CalendarClock },
  { to: '/audit', label: 'Audit Log', icon: ClipboardList },
  { to: '/admin', label: 'Admin & Settings', icon: Settings },
]

export default function Layout() {
  const { data: admin } = useAdminSettings()
  const flags = (admin?.feature_flags ?? {}) as Record<string, boolean>
  const demo = flags.demo_mode

  return (
    <div className="flex h-full">
      <aside className="flex w-64 shrink-0 flex-col border-r border-edge bg-ink p-4">
        <div className="mb-6 flex items-center gap-2 px-2">
          <ShieldAlert className="text-accent" size="22" />
          <div>
            <div className="text-sm font-semibold text-white">Regression Validation</div>
            <div className="text-[11px] text-muted">Databricks connectivity platform</div>
          </div>
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
