import { Route, Routes } from 'react-router-dom'

import Layout from './components/Layout'
import WhatsInteresting from './pages/WhatsInteresting'
import Runs from './pages/Runs'
import RunDetail from './pages/RunDetail'
import TestCatalog from './pages/TestCatalog'
import TestStudio from './pages/TestStudio'
import Suites from './pages/Suites'
import Targets from './pages/Targets'
import Schedules from './pages/Schedules'
import Audit from './pages/Audit'
import Admin from './pages/Admin'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<WhatsInteresting />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:runId" element={<RunDetail />} />
        <Route path="tests" element={<TestCatalog />} />
        <Route path="studio" element={<TestStudio />} />
        <Route path="authoring" element={<TestStudio />} />
        <Route path="suites" element={<Suites />} />
        <Route path="targets" element={<Targets />} />
        <Route path="schedules" element={<Schedules />} />
        {/* Legacy /analytics path kept as an alias so the run-completion webhook's
            deep link (ADR-0016) still lands on the "What's Interesting" view. */}
        <Route path="analytics" element={<WhatsInteresting />} />
        <Route path="audit" element={<Audit />} />
        <Route path="admin" element={<Admin />} />
      </Route>
    </Routes>
  )
}
