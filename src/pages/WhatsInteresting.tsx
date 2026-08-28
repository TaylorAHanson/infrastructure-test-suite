// The "What's Interesting" triage view (ADR-0009, #35). Two lanes on one screen:
// a height-bounded Recent-deployments list with infinite scroll, and an
// Overnight lane for drift not tied to a deploy. Nothing auto-expands — every
// finding is a collapsed fold opened on click — and the non-actionable counts
// sit behind one "Everything else" footer. Opening a finding shows its
// `bucket @ stage`, its last-green → first-bad provenance thread, and any flaky
// advisory. Renders the two-lane findings feed (`GET /findings`); the
// troubleshooting/RCA help rides on the app-wide Assistant shelf (ADR-0012),
// which this page grounds by declaring the open finding as its page context.
import { useState } from 'react'
import { AlertTriangle, ChevronRight, GitBranch, Moon, Rocket } from 'lucide-react'

import { PageHeader } from '../components/PageHeader'
import { useFindings } from '../hooks/queries'
import { useDeclarePageContext } from '../lib/assistantContext'
import { timeAgo } from '../lib/format'
import type { Finding, FindingsLane, ProvenanceThread } from '../types'

const RECENT = 'recent-deployments'
const OVERNIGHT = 'overnight'
// How many Recent-deployments folds to reveal per infinite-scroll page.
const PAGE = 8

/** A stable per-series identity so a user-opened fold stays open across polls. */
function findingKey(f: Finding): string {
  return `${f.check_id}::${f.target_id ?? '∅'}`
}

function shortSha(sha?: string | null): string {
  if (!sha || sha === 'unknown') return ''
  return sha.slice(0, 7)
}

/** The "since …" clause on a fold summary: keyed to the provenance boundary (a
 *  commit sha, or a non-deploy event ref); an honest "oscillating" for a flaky
 *  finding with no single first-bad. */
function sinceLabel(f: Finding): string {
  const ref = f.provenance?.boundary_ref
  if (ref?.kind === 'commit') return `since ${shortSha(ref.value) || ref.value}`
  if (ref?.kind === 'event') return `since ${ref.value}`
  if (f.flaky) return 'oscillating — no single first-bad'
  const sha = shortSha(f.git_sha)
  return sha ? `since ${sha}` : ''
}

/** Severity dot: flaky is advisory (purple), BLOCKED_BY_SAFETY is loud-but-not-a-
 *  functional-failure (warn), everything else actionable is a hard fail (red). */
function dotClass(f: Finding): string {
  if (f.flaky) return 'bg-flaky'
  if (f.verdict === 'BLOCKED_BY_SAFETY') return 'bg-warn'
  return 'bg-fail'
}

function ThreadNode({ tone, label, when }: { tone: 'good' | 'bad'; label: string; when: string }) {
  const border = tone === 'good' ? 'border-pass/40' : 'border-fail/40'
  const labelColor = tone === 'good' ? 'text-pass' : 'text-fail'
  return (
    <div className={`flex flex-col gap-0.5 rounded-lg border ${border} bg-ink px-2.5 py-1.5`}>
      <span className={`font-mono ${labelColor}`}>{label || '—'}</span>
      <span className="text-[11px] text-muted">{when}</span>
    </div>
  )
}

/** The last-green → first-bad troubleshooting spine (#34) — *when did this start
 *  failing?* expressed as diff-vs-last-good, keyed to a commit or event. */
function ProvenanceThreadView({ thread }: { thread?: ProvenanceThread | null }) {
  if (!thread || (!thread.last_green && !thread.first_bad)) {
    return <div className="text-xs italic text-muted">No prior history retained for this series.</div>
  }
  const { last_green: lastGreen, first_bad: firstBad, boundary_ref: boundary } = thread
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {lastGreen ? (
        <ThreadNode
          tone="good"
          label={shortSha(lastGreen.git_sha) || 'green'}
          when={`last green · ${timeAgo(lastGreen.created_at)}`}
        />
      ) : (
        <ThreadNode tone="good" label="—" when="no green on record" />
      )}
      <span className="text-muted">→</span>
      {boundary && (
        <>
          <span className="italic text-muted">
            {boundary.kind === 'commit' ? shortSha(boundary.value) || boundary.value : boundary.value}
          </span>
          <span className="text-muted">→</span>
        </>
      )}
      {firstBad ? (
        <ThreadNode
          tone="bad"
          label={shortSha(firstBad.git_sha) || firstBad.event_ref || 'bad'}
          when={`first bad · ${timeAgo(firstBad.created_at)}`}
        />
      ) : (
        <ThreadNode tone="bad" label="—" when="not yet failing" />
      )}
    </div>
  )
}

/** One finding — a collapsed fold. Nothing auto-expands; opening it declares the
 *  finding as page context so the Assistant shelf can suggest a cause. */
function FindingFold({ f, onOpen }: { f: Finding; onOpen: (f: Finding | null) => void }) {
  return (
    <details
      className="group border-b border-edge/60 last:border-b-0"
      onToggle={(e) => onOpen(e.currentTarget.open ? f : null)}
    >
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
        <span className={`h-2 w-2 shrink-0 rounded-full ${dotClass(f)}`} />
        <span className="font-medium text-white">{f.check_name}</span>
        {f.workspace_name && (
          <span className="truncate font-mono text-xs text-muted">{f.workspace_name}</span>
        )}
        {f.flaky && <span className="badge bg-flaky/15 text-flaky">flaky · advisory</span>}
        <span className="ml-auto whitespace-nowrap text-xs text-muted">{sinceLabel(f)}</span>
        <ChevronRight size={16} className="shrink-0 text-muted transition group-open:rotate-90" />
      </summary>
      <div className="flex flex-col gap-3 border-t border-edge/60 bg-ink/40 px-4 py-3 pl-9">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="badge bg-fail/15 font-mono text-fail">{f.bucket_at_stage ?? f.verdict}</span>
          <span>
            check <span className="font-mono text-gray-200">{f.check_id}</span>
          </span>
          <span className={`badge ${f.gating ? 'bg-pass/15 text-pass' : 'bg-warn/15 text-warn'}`}>
            {f.gating ? 'gating' : 'advisory'}
          </span>
          <span className="font-mono">{f.evaluator}</span>
        </div>
        <ProvenanceThreadView thread={f.provenance} />
        {f.message && <div className="text-xs text-muted">{f.message}</div>}
        {f.flaky && (
          <div className="flex items-center gap-2 text-xs text-flaky">
            <AlertTriangle size={13} className="shrink-0" />
            Recent deterministic verdicts alternate — advisory only, never gates the pipeline (ADR-0008).
          </div>
        )}
      </div>
    </details>
  )
}

/** Lane 1 — Recent deployments: height-bounded with infinite scroll, so deploy
 *  volume never pushes the rest of the page down (ADR-0009 §4). */
function RecentLane({ lane, onOpen }: { lane: FindingsLane; onOpen: (f: Finding | null) => void }) {
  const [visible, setVisible] = useState(PAGE)
  const shown = lane.findings.slice(0, visible)
  const atEnd = visible >= lane.findings.length

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 48 && !atEnd) {
      setVisible((v) => Math.min(v + PAGE, lane.findings.length))
    }
  }

  return (
    <div className="max-h-[360px] overflow-y-auto rounded-xl border border-edge bg-panel" onScroll={onScroll}>
      {lane.findings.length === 0 ? (
        <div className="px-4 py-6 text-sm text-muted">
          No deploy-attributed regressions in the window. CI-triggered runs are all green.
        </div>
      ) : (
        <>
          {shown.map((f) => (
            <FindingFold key={findingKey(f)} f={f} onOpen={onOpen} />
          ))}
          <div className="px-4 py-3 text-center text-[11px] italic text-muted">
            {atEnd ? '— start of window —' : 'scroll for older ↓'}
          </div>
        </>
      )}
    </div>
  )
}

/** Lane 2 — Overnight: fleet drift not attributable to a deploy. Not height-
 *  bounded (it's the smaller, deliberate lane, ADR-0009 §5). */
function OvernightLane({ lane, onOpen }: { lane: FindingsLane; onOpen: (f: Finding | null) => void }) {
  if (lane.findings.length === 0) {
    return (
      <div className="rounded-xl border border-edge bg-panel px-4 py-6 text-sm text-muted">
        Nothing drifted overnight — no unattributed regressions.
      </div>
    )
  }
  return (
    <div className="rounded-xl border border-edge bg-panel">
      {lane.findings.map((f) => (
        <FindingFold key={findingKey(f)} f={f} onOpen={onOpen} />
      ))}
    </div>
  )
}

export default function WhatsInteresting() {
  const { data, isLoading, isError, error } = useFindings()
  const [openFinding, setOpenFinding] = useState<Finding | null>(null)

  // Ground the app-wide Assistant shelf in the finding under triage (ADR-0012).
  useDeclarePageContext({
    page: 'whats-interesting',
    finding: openFinding ? { check_id: openFinding.check_id, target_id: openFinding.target_id ?? null } : null,
  })

  const lanes = data?.lanes ?? []
  const recent = lanes.find((l) => l.lane === RECENT) ?? { lane: RECENT, findings: [], everything_else: 0 }
  const overnight =
    lanes.find((l) => l.lane === OVERNIGHT) ?? { lane: OVERNIGHT, findings: [], everything_else: 0 }
  const everythingElse = recent.everything_else + overnight.everything_else
  const windowHours = data?.window_hours ?? 24

  return (
    <div>
      <PageHeader
        title="What's Interesting"
        subtitle="Two questions, one screen: what recent deployments did, and what drifted while you were away."
      />

      {isLoading && <div className="card text-sm text-muted">Loading findings…</div>}
      {isError && (
        <div className="card border-fail/40 text-sm text-fail">
          Couldn’t load the findings feed: {(error as Error)?.message ?? 'unknown error'}
        </div>
      )}

      {data && (
        <>
          <section className="mb-6">
            <div className="mb-2 flex items-center gap-3 border-l-[3px] border-accent pl-3">
              <Rocket size={16} className="text-accent" />
              <div>
                <div className="text-[15px] font-bold text-white">Recent deployments</div>
                <div className="text-xs text-muted">
                  last {windowHours}h · newest first ·{' '}
                  <span className="text-fail">{recent.findings.length} needing attention</span>
                </div>
              </div>
              <span className="ml-auto text-xs text-muted">scroll for older ↓</span>
            </div>
            <RecentLane lane={recent} onOpen={setOpenFinding} />
          </section>

          <section className="mb-6">
            <div className="mb-2 flex items-center gap-3">
              <Moon size={16} className="text-muted" />
              <div>
                <div className="text-[15px] font-bold text-white">Overnight — while you were away</div>
                <div className="text-xs text-muted">drift not tied to a deploy · defaults here when unattributed</div>
              </div>
              <span className="ml-auto text-xs text-muted">{overnight.findings.length} changed</span>
            </div>
            <OvernightLane lane={overnight} onOpen={setOpenFinding} />
          </section>

          <details className="group rounded-xl border border-dashed border-edge">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
              <ChevronRight size={16} className="transition group-open:rotate-90" />
              <GitBranch size={14} className="text-muted" />
              Everything else — <b className="text-white">{everythingElse}</b> nominal / recovered series, collapsed
            </summary>
            <div className="border-t border-edge/60 px-4 py-3 text-xs text-muted">
              These <b className="text-white">{everythingElse}</b> <code className="font-mono">(target, check)</code> series
              are either still green or have recovered — non-actionable, so they’re kept out of the two lanes above
              (ADR-0009 §6). {recent.everything_else} in Recent deployments, {overnight.everything_else} overnight.
            </div>
          </details>
        </>
      )}
    </div>
  )
}
