import { Link } from 'react-router-dom'
import { CalendarClock } from 'lucide-react'

import { PageHeader } from '../components/PageHeader'

// The nightly run is the app's only schedule. Its cron comes from the bundle
// (`nightly_cron` in databricks.yml), and the poller submits it through the same
// run service as POST /runs (backend/app/services/nightly.py). This page is a
// read-only explanation; it makes no API calls.
export default function Schedules() {
  return (
    <div>
      <PageHeader
        title="Schedules"
        subtitle="The nightly run is set in the deployment bundle"
      />

      <div className="card mb-4 flex items-start gap-3 border-info/30 bg-info/5">
        <CalendarClock size="20" className="mt-0.5 shrink-0 text-info" />
        <div className="text-sm text-gray-200">
          <p className="font-semibold text-white">The app runs a full regression every night.</p>
          <p className="mt-1 text-muted">
            On the <span className="font-mono text-info">nightly_cron</span> schedule from the
            deployment bundle (<span className="font-mono">databricks.yml</span>), the app starts one
            full Run of every active Test against every Target. It goes through the same run engine
            as the CI Action, the API and this GUI.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Fact label="Trigger" value="The app, on a cron" />
        <Fact label="Cadence" value="Five-field cron · UTC (var.nightly_cron, default 07:00)" />
        <Fact label="Depth" value="full — through OPERATE / VERIFY" />
        <Fact label="Selection" value="All Active Tests against all targets" />
        <Fact label="Missed runs" value="Skipped if the app was down at the scheduled time" />
        <Fact label="Pause / edit" value="databricks.yml → redeploy the bundle" />
      </div>

      <div className="card mt-4">
        <div className="card-title mb-2">Where to look</div>
        <ul className="space-y-2 text-sm text-gray-200">
          <li>
            · <Link to="/runs" className="text-accent hover:underline">Runs</Link> — every nightly
            run appears here with a <span className="font-mono">NIGHTLY</span> source, phase and
            verdict, like any other run.
          </li>
          <li>
            · To change the time or pause it, set <span className="font-mono">nightly_cron</span> or{' '}
            <span className="font-mono">schedule_pause_status</span> in{' '}
            <span className="font-mono">databricks.yml</span> and redeploy.
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
