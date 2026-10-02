import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Play, ShieldAlert } from 'lucide-react'

import { useChecks, useSubmitRun, useSuites, useTargets, useTests } from '../hooks/queries'
import type { Target } from '../types'

// Launch a Run against the rebuilt Run API (ADR-0005): pick a Suite (blank =
// every Active Test), a Target (blank = all registered targets — the per-target
// safety gate still fires per target), and a depth. Depth defaults to `quick`;
// `full` is an explicit opt-in (ADR-0007).
//
// The friction ceremony for the R2-on-`prod-for-us` safety override (ADR-0004
// §5/§0010, #81) lives here — the run control — mirroring the safety-class
// downgrade ceremony on the Targets page. It is offered only when this human
// GUI caller has picked one specific `prod-for-us` target and the selected
// scope contains at least one R2 (destructive) Check; completing it attaches
// the override to the same `POST /runs` submission as an ordinary run.
const CONFIRM_PHRASE = 'i understand'

export function RunLauncher({ label, onLaunched }: { label?: string; onLaunched?: (id: string) => void }) {
  const navigate = useNavigate()
  const { data: suites } = useSuites()
  const { data: targets } = useTargets()
  const { data: tests } = useTests()
  const { data: checks } = useChecks()
  const submit = useSubmitRun()

  const [suiteId, setSuiteId] = useState('')
  const [targetId, setTargetId] = useState('')
  const [depth, setDepth] = useState<'quick' | 'full'>('quick')
  const [error, setError] = useState<string | null>(null)
  const [showCeremony, setShowCeremony] = useState(false)

  const selectedTarget = targets?.find((t) => t.id === targetId) ?? null

  // Whether the current selection scope contains at least one Check whose
  // effective risk tier is R2. Mirrors the engine's own selection (blank suite
  // = every enabled, Active Test — a Draft never runs, even if named
  // explicitly, `_selections`/ADR-0017): a human-confirmed tier (captured
  // suggest-then-confirm, ADR-0017 §2c) can only raise the coarse verb guess,
  // never lower it, so the higher of the two previews what the gate will
  // weigh — the GUI only *previews*, the engine decides.
  const scopeHasR2 = useMemo(() => {
    if (!checks || !tests) return false
    const checkTier = (checkId: string): string | undefined => {
      const c = checks.find((c) => c.id === checkId)
      return c && c.enabled ? c.confirmed_risk_tier || c.risk_tier : undefined
    }
    const suite = suiteId ? suites?.find((s) => s.id === suiteId) : undefined
    if (suiteId && !suite) return false
    const scopedTests = tests.filter((t) => {
      if (!t.enabled || t.status !== 'active') return false
      return suite ? suite.test_ids.includes(t.id) : true
    })
    const checkIds = new Set(scopedTests.flatMap((t) => t.members.map((m) => m.check_id)))
    return [...checkIds].some((id) => checkTier(id) === 'R2')
  }, [checks, tests, suites, suiteId])

  // The override is only offerable against one concretely named target — its
  // typed confirmation must match that name — so "all targets" never offers it.
  const overrideEligible = !!selectedTarget && selectedTarget.safety_class === 'prod-for-us' && scopeHasR2

  async function doSubmit(override?: { target_name: string; reason: string; confirmed: boolean }) {
    setError(null)
    try {
      const res = await submit.mutateAsync({
        suite_ids: suiteId ? [suiteId] : [],
        target_ids: targetId ? [targetId] : [],
        depth,
        requested_by: 'ui-user',
        request_source: 'UI_MANUAL',
        ...(override ? { override } : {}),
      })
      if (onLaunched) onLaunched(res.run_id)
      else navigate(`/runs/${res.run_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to submit run.')
    }
  }

  return (
    <div className="card flex flex-wrap items-end gap-3">
      {label && <span className="row-label">{label}</span>}
      <div className="flex flex-col gap-1">
        <label className="card-title">Suite (blank = all Active Tests)</label>
        <select className="input" value={suiteId} onChange={(e) => setSuiteId(e.target.value)}>
          <option value="">All Tests</option>
          {suites?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="card-title">Target (blank = all)</label>
        <select className="input" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
          <option value="">All targets</option>
          {targets?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="card-title">Depth</label>
        <select
          className="input"
          value={depth}
          onChange={(e) => setDepth(e.target.value as 'quick' | 'full')}
        >
          <option value="quick">Quick (connectivity end)</option>
          <option value="full">Full (through OPERATE / VERIFY)</option>
        </select>
      </div>
      <button className="btn btn-accent" onClick={() => doSubmit()} disabled={submit.isPending}>
        <Play size="16" />
        {submit.isPending ? 'Submitting...' : 'Run'}
      </button>
      {submit.isSuccess && !error && (
        <span className="text-sm text-pass">Run submitted - opening run...</span>
      )}
      {error && <span className="w-full text-sm text-fail">{error}</span>}

      {overrideEligible && (
        <div className="flex w-full items-start gap-2 rounded border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          <AlertTriangle size="16" className="mt-0.5 shrink-0" />
          <span className="flex-1">
            This scope includes an R2 (destructive) operation against{' '}
            <b>{selectedTarget!.name}</b>, a <b>prod-for-us</b> target — it will run as{' '}
            <b>BLOCKED_BY_SAFETY</b> unless you authorize a one-time override for this run.
          </span>
          <button
            className="btn shrink-0 border-warn/40 text-warn hover:bg-warn/20"
            onClick={() => setShowCeremony(true)}
            disabled={submit.isPending}
          >
            <ShieldAlert size="14" />
            Override &amp; run
          </button>
        </div>
      )}

      {showCeremony && selectedTarget && (
        <OverrideCeremony
          target={selectedTarget}
          pending={submit.isPending}
          onCancel={() => setShowCeremony(false)}
          onConfirm={(reason) => {
            setShowCeremony(false)
            doSubmit({ target_name: selectedTarget.name, reason, confirmed: true })
          }}
        />
      )}
    </div>
  )
}

// --------------------------- Friction ceremony ---------------------------
// The override defeats the safety gate for exactly one elevated tier (R2) on a
// `prod-for-us` target (ADR-0004 §5) — never R3, which stays globally forbidden
// through the blocklist regardless. Guarded by friction, not permission:
// escalating warnings + a concrete consequence + a required reason + an
// AWS-style typed confirmation (ADR-0010). Abandoning or mistyping it never
// calls the submit mutation — nothing here fires until `onConfirm` runs.
function OverrideCeremony({
  target, pending, onCancel, onConfirm,
}: {
  target: Target
  pending: boolean
  onCancel: () => void
  onConfirm: (reason: string) => void
}) {
  const [typed, setTyped] = useState('')
  const [reason, setReason] = useState('')
  const ready = typed.trim().toLowerCase() === CONFIRM_PHRASE && reason.trim().length > 0

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
    >
      <div className="card w-full max-w-lg border border-fail/40">
        <div className="mb-3 flex items-center gap-2 text-fail">
          <ShieldAlert size="20" />
          <div className="card-title text-fail">Safety-gate override — this run only</div>
        </div>

        <div className="mb-3 flex items-start gap-2 rounded border border-fail/40 bg-fail/10 p-3 text-sm text-fail">
          <AlertTriangle size="16" className="mt-0.5 shrink-0" />
          <span>
            You are about to authorize an <b>R2 (destructive)</b> operation — such as deleting a
            cluster, job, or schema — against <b>{target.name}</b>, a <b>prod-for-us</b> target.
            This target is normally capped at R1 (safe mutations only); this override lifts that
            cap for this run alone.
          </span>
        </div>

        <div className="mb-3 flex items-start gap-2 rounded border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          <AlertTriangle size="16" className="mt-0.5 shrink-0" />
          <span>
            This is not a permission check — it is a deliberate, audited act. It authorizes exactly
            one elevated tier (R2) on this one target for this one run; it is never available to
            CI/automation, and R3 (catastrophic) operations stay globally forbidden regardless.
          </span>
        </div>

        <label className="card-title">Reason (recorded in the audit log)</label>
        <input
          className="input mb-3 mt-1 w-full"
          placeholder="Why does this run need the override?"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />

        <label className="card-title">
          Type <span className="font-mono text-white">{CONFIRM_PHRASE}</span> to confirm
        </label>
        <input
          className="input mb-4 mt-1 w-full font-mono"
          placeholder={CONFIRM_PHRASE}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          autoFocus
        />

        <div className="flex justify-end gap-2">
          <button className="btn" onClick={onCancel} disabled={pending}>
            Cancel
          </button>
          <button
            className="btn btn-accent disabled:opacity-40"
            disabled={!ready || pending}
            onClick={() => onConfirm(reason.trim())}
          >
            {pending ? 'Submitting…' : 'Authorize override & run'}
          </button>
        </div>
      </div>
    </div>
  )
}
