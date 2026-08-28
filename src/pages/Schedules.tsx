import { Link } from 'react-router-dom'
import { CalendarClock, ExternalLink } from 'lucide-react'

import { PageHeader } from '../components/PageHeader'

// There is NO in-app scheduler (the prototype's /schedules CRUD is gone). Nightly
// regression is a Databricks Job defined in the bundle (`suite_runner` in
// databricks.yml) that is just another authenticated caller of the shared Run
// API (ADR-0005) — it starts a run and polls it fail-closed. This page is a
// read-only reframe of that reality; it makes no API calls.
export default function Schedules() {
  return (
    <div>
      <PageHeader
        title="Schedules"
        subtitle="Scheduling lives in the deployment bundle, not in the app"
      />

      <div className="card mb-4 flex items-start gap-3 border-info/30 bg-info/5">
        <CalendarClock size="20" className="mt-0.5 shrink-0 text-info" />
        <div className="text-sm text-gray-200">
          <p className="font-semibold text-white">Nightly regression is a scheduled Databricks Job.</p>
          <p className="mt-1 text-muted">
            The app does not own a scheduler. The nightly run is the{' '}
            <span className="font-mono text-info">suite_runner</span> job declared in the deployment
            bundle (<span className="font-mono">databricks.yml</span>). On its cron it invokes the
            same <span className="font-mono">POST /runs</span> Run API the CI Action and this GUI
            use — there is exactly one run engine behind all three triggers.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Fact label="Trigger" value="Databricks Job (bundle-managed)" />
        <Fact label="Cadence" value="Quartz cron · UTC (var.nightly_cron)" />
        <Fact label="Depth" value="full — through OPERATE / VERIFY" />
        <Fact label="Selection" value="All Active Tests against all targets" />
        <Fact label="Contract" value="Authenticated caller of the Run API (ADR-0005)" />
        <Fact label="Pause / edit" value="databricks.yml → redeploy the bundle" />
      </div>

      <div className="card mt-4">
        <div className="card-title mb-2">Where to look instead</div>
        <ul className="space-y-2 text-sm text-gray-200">
          <li>
            · <Link to="/runs" className="text-accent hover:underline">Runs</Link> — every nightly
            run appears here with a <span className="font-mono">NIGHTLY</span> source, phase and
            verdict, like any other run.
          </li>
          <li className="flex items-center gap-1">
            · <span>To change the cadence or pause it, edit the job in</span>
            <span className="font-mono">databricks.yml</span>
            <span>and redeploy the bundle</span>
            <ExternalLink size="12" className="text-muted" />
          </li>
        </ul>
      </div>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="card py-3">
      <div className="card-title">{label}</div>
      <div className="mt-0.5 text-sm text-white">{value}</div>
    </div>
  )
}
