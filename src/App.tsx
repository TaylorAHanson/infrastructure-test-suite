import { Route, Routes } from 'react-router-dom'

import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Runs from './pages/Runs'
import RunDetail from './pages/RunDetail'
import TestCatalog from './pages/TestCatalog'
import Suites from './pages/Suites'
import Targets from './pages/Targets'
import Schedules from './pages/Schedules'
import Analytics from './pages/Analytics'
import Audit from './pages/Audit'
import Admin from './pages/Admin'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/:runId" element={<RunDetail />} />
        <Route path="tests" element={<TestCatalog />} />
        <Route path="suites" element={<Suites />} />
        <Route path="targets" element={<Targets />} />
        <Route path="schedules" element={<Schedules />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="audit" element={<Audit />} />
        <Route path="admin" element={<Admin />} />
      </Route>
    </Routes>
  )
}
