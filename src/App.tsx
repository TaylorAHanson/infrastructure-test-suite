import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import Layout from './components/Layout'
import WhatsInteresting from './pages/WhatsInteresting'
import Runs from './pages/Runs'
import RunDetail from './pages/RunDetail'
import Tests from './pages/Tests'
import Targets from './pages/Targets'
import Schedules from './pages/Schedules'
import Audit from './pages/Audit'
import Branding from './pages/Branding'
import Notifications from './pages/Notifications'
import AssistantSettings from './pages/AssistantSettings'
import ReleaseNotes from './pages/ReleaseNotes'

// The editor pulls in CodeMirror; load it only when a test is opened for editing.
const TestEditor = lazy(() => import('./pages/TestEditor'))
// The guide bundles every doc page; load it only when someone opens it.
const Guide = lazy(() => import('./pages/Guide'))

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<WhatsInteresting />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:runId" element={<RunDetail />} />
        <Route path="tests" element={<Tests />} />
        <Route path="tests/new" element={<Suspense fallback={null}><TestEditor /></Suspense>} />
        <Route path="tests/:testId/edit" element={<Suspense fallback={null}><TestEditor /></Suspense>} />
        <Route path="audit" element={<Audit />} />
        <Route path="guide/*" element={<Suspense fallback={null}><Guide /></Suspense>} />
        <Route path="settings">
          <Route index element={<Navigate to="branding" replace />} />
          <Route path="branding" element={<Branding />} />
          <Route path="targets" element={<Targets />} />
          <Route path="schedules" element={<Schedules />} />
          <Route path="notifications" element={<Notifications />} />
          <Route path="assistant" element={<AssistantSettings />} />
          <Route path="release-notes" element={<ReleaseNotes />} />
        </Route>
        {/* Legacy /analytics path kept as an alias so the run-completion webhook's
            deep link (ADR-0016) still lands on the "What's Interesting" view. */}
        <Route path="analytics" element={<WhatsInteresting />} />
        {/* Pages folded into Tests or Settings — kept as redirects so old links land. */}
        <Route path="studio" element={<Navigate to="/tests" replace />} />
        <Route path="authoring" element={<Navigate to="/tests" replace />} />
        <Route path="suites" element={<Navigate to="/tests" replace />} />
        <Route path="targets" element={<Navigate to="/settings/targets" replace />} />
        <Route path="schedules" element={<Navigate to="/settings/schedules" replace />} />
        <Route path="admin" element={<Navigate to="/settings" replace />} />
      </Route>
    </Routes>
  )
}
