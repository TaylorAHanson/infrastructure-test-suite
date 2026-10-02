import { useMemo, useState } from 'react'
import { AlertTriangle, Plus, ShieldAlert, Trash2, X } from 'lucide-react'

import { PageHeader } from '../components/PageHeader'
import {
  useCreateTarget,
  useDeleteTarget,
  useTargets,
  useUpdateTarget,
  type CreateTargetBody,
} from '../hooks/queries'
import { errorMessage } from '../lib/api'
import type { AssumableRole, SafetyClass, Target } from '../types'

/** The register form collects scope + key separately so a scope-only value
 *  (the common typo) never gets posted as the identity handle. */
type TargetForm = Omit<CreateTargetBody, 'identity_handle'> & {
  identity_scope: string
  identity_key: string
}

// The Targets page on the rebuilt model (ADR-0003/0004/0018): a Target is a
// workspace under test with an authoritative safety class, a per-target identity
// handle, and registered assumable roles. There is **no role gate** (ADR-0010) —
// any authenticated human can register/edit — but the two safety-gate-defeating
// edits are guarded by a friction ceremony. The safety-class *downgrade* ceremony
// lives here (the override ceremony lives on the run control).

const CLASSES: SafetyClass[] = ['read-only', 'prod-for-us', 'sandbox']

// The class → max-risk-tier order, mirroring the backend matrix (ADR-0004 §1):
// read-only → R0, prod-for-us → R1, sandbox → R2. A *downgrade* is any change
// that raises this order (widens the blast radius).
const TIER_ORDER: Record<SafetyClass, number> = {
  'read-only': 0,
  'prod-for-us': 1,
  sandbox: 2,
}

const CLASS_BADGE: Record<SafetyClass, string> = {
  'read-only': 'bg-info/15 text-info',
  'prod-for-us': 'bg-fail/15 text-fail',
  sandbox: 'bg-warn/20 text-warn',
}

// What the new class will newly permit — the concrete consequence the ceremony
// states plainly before the human confirms.
const CONSEQUENCE: Record<SafetyClass, string> = {
  'read-only': 'only read-only (R0) operations',
  'prod-for-us': 'safe mutations (R1) such as creating a cluster or job',
  sandbox: 'destructive (R2) operations such as deleting a cluster, job, or schema',
}

function isDowngrade(from: SafetyClass, to: SafetyClass): boolean {
  return TIER_ORDER[to] > TIER_ORDER[from]
}

// A Tag is a plain `key=value` navigation filter (CONTEXT.md) — never a query
// language. `environment` is just a tag among others here; it gets no special
// casing, so a target's tags render/filter identically regardless of key.
function tagEntries(target: Target): string[] {
  const tags = target.tags
  if (!tags) return []
  return Object.entries(tags)
    .map(([k, v]) => `${k}=${String(v)}`)
    .sort()
}

function ClassBadge({ value }: { value: SafetyClass }) {
  return <span className={`badge ${CLASS_BADGE[value]}`}>{value}</span>
}

function TagsCell({ target }: { target: Target }) {
  const tags = tagEntries(target)
  if (!tags.length) return <>-</>
  return (
    <>
      {tags.map((tag) => (
        <span key={tag} className="badge mr-1 bg-ink-soft text-muted">{tag}</span>
      ))}
    </>
  )
}

const EMPTY: TargetForm = {
  name: '',
  workspace_name: '',
  host: '',
  safety_class: 'read-only',
  identity_scope: '',
  identity_key: '',
  assumable_roles: [],
}

export default function Targets() {
  const { data: targets } = useTargets()
  const create = useCreateTarget()
  const del = useDeleteTarget()
  const update = useUpdateTarget()

  const [form, setForm] = useState<TargetForm>(EMPTY)
  const [error, setError] = useState<string | null>(null)
  // The target whose safety-class downgrade is awaiting the friction ceremony.
  const [ceremony, setCeremony] = useState<{ target: Target; to: SafetyClass } | null>(null)

  // Tags are a navigation filter, not a query language (CONTEXT.md): a plain
  // toggle set of `key=value` chips, ANDed together. One click adds or removes
  // a chip — no modifier keys — so the same gesture composes a multi-tag
  // filter for downstream run/selection flows.
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set())

  const allTags = useMemo(() => {
    const seen = new Set<string>()
    for (const t of targets ?? []) for (const tag of tagEntries(t)) seen.add(tag)
    return [...seen].sort()
  }, [targets])

  const filteredTargets = useMemo(() => {
    if (selectedTags.size === 0) return targets ?? []
    return (targets ?? []).filter((t) => {
      const own = new Set(tagEntries(t))
      return [...selectedTags].every((tag) => own.has(tag))
    })
  }, [targets, selectedTags])

  function toggleTag(tag: string) {
    setSelectedTags((prev) => {
      const next = new Set(prev)
      if (next.has(tag)) next.delete(tag)
      else next.add(tag)
      return next
    })
  }

  function set<K extends keyof TargetForm>(key: K, value: TargetForm[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function submit() {
    setError(null)
    if (!form.name.trim()) return setError('Name is required.')
    const scope = form.identity_scope.trim()
    const key = form.identity_key.trim()
    if (!scope || !key) {
      return setError('Identity needs both a secret scope and a key (not the app or workspace name).')
    }
    if (scope.includes('/') || key.includes('/')) {
      return setError('Scope and key must not contain slashes.')
    }
    try {
      await create.mutateAsync({
        name: form.name.trim(),
        workspace_name: form.workspace_name?.trim() || null,
        host: form.host?.trim() || null,
        safety_class: form.safety_class,
        identity_handle: `${scope}/${key}`,
        assumable_roles: form.assumable_roles ?? [],
      })
      setForm(EMPTY)
    } catch (e) {
      setError(errorMessage(e) || 'Failed to register target.')
    }
  }

  // Changing a target's class inline: a downgrade routes through the ceremony
  // modal; a narrowing (upgrade) applies immediately (it defeats no gate).
  async function changeClass(target: Target, to: SafetyClass) {
    if (to === target.safety_class) return
    if (isDowngrade(target.safety_class, to)) {
      setCeremony({ target, to })
      return
    }
    await update.mutateAsync({ id: target.id, body: { safety_class: to } })
  }

  return (
    <div>
      <PageHeader
        title="Targets"
        subtitle="Workspaces under test — each with an authoritative safety class, a per-target identity, and registered assumable roles."
      />

      <AddTargetForm form={form} set={set} submit={submit} pending={create.isPending} error={error}
                     setRoles={(r) => set('assumable_roles', r)} />

      {allTags.length > 0 && (
        <TagFilterBar tags={allTags} selected={selectedTags} onToggle={toggleTag}
                      onClear={() => setSelectedTags(new Set())} />
      )}

      <div className="card overflow-hidden p-0">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink">
            <tr>
              <th className="th">Name</th>
              <th className="th">Workspace / Host</th>
              <th className="th">Safety class</th>
              <th className="th">Identity handle</th>
              <th className="th">Assumable roles</th>
              <th className="th">Tags</th>
              <th className="th"></th>
            </tr>
          </thead>
          <tbody>
            {filteredTargets.map((t) => (
              <tr key={t.id} className={`border-b border-edge/50 hover:bg-ink-soft ${t.enabled ? '' : 'opacity-50'}`}>
                <td className="td text-white">
                  {t.name}
                  {!t.enabled && <span className="ml-2 text-xs text-muted">(deregistered)</span>}
                </td>
                <td className="td text-muted">{t.workspace_name ?? '-'}{t.host ? ` · ${t.host}` : ''}</td>
                <td className="td">
                  <select
                    className={`input py-1 text-xs font-medium ${CLASS_BADGE[t.safety_class]}`}
                    value={t.safety_class}
                    disabled={!t.enabled || update.isPending}
                    onChange={(e) => changeClass(t, e.target.value as SafetyClass)}
                  >
                    {CLASSES.map((c) => (
                      <option key={c} value={c} className="bg-ink text-white">{c}</option>
                    ))}
                  </select>
                </td>
                <td className="td font-mono text-xs text-muted">{t.identity_handle ?? '-'}</td>
                <td className="td text-muted">
                  {t.assumable_roles?.length
                    ? t.assumable_roles.map((r) => (
                        <span key={r.group_id} className="badge mr-1 bg-skip/20 text-muted">
                          {r.label || r.group_id} · {r.safety_class}
                        </span>
                      ))
                    : '-'}
                </td>
                <td className="td text-muted">
                  <TagsCell target={t} />
                </td>
                <td className="td text-right">
                  <button
                    className="text-muted hover:text-fail disabled:opacity-40"
                    title="Deregister target"
                    onClick={() => del.mutate(t.id)}
                    disabled={del.isPending || !t.enabled}
                  >
                    <Trash2 size="15" />
                  </button>
                </td>
              </tr>
            ))}
            {!targets?.length && (
              <tr>
                <td className="td text-sm text-muted" colSpan={7}>No targets yet. Register one above.</td>
              </tr>
            )}
            {!!targets?.length && !filteredTargets.length && (
              <tr>
                <td className="td text-sm text-muted" colSpan={7}>
                  No targets match the selected tags.{' '}
                  <button className="text-info underline" onClick={() => setSelectedTags(new Set())}>
                    Clear filters
                  </button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {ceremony && (
        <DowngradeCeremony
          target={ceremony.target}
          to={ceremony.to}
          pending={update.isPending}
          onCancel={() => setCeremony(null)}
          onConfirm={async (reason) => {
            await update.mutateAsync({
              id: ceremony.target.id,
              body: {
                safety_class: ceremony.to,
                ceremony: { typed_confirmation: 'i understand', reason },
              },
            })
            setCeremony(null)
          }}
        />
      )}
    </div>
  )
}

// --------------------------- Add-target form ---------------------------
function AddTargetForm({
  form, set, submit, pending, error, setRoles,
}: {
  form: TargetForm
  set: <K extends keyof TargetForm>(key: K, value: TargetForm[K]) => void
  submit: () => void
  pending: boolean
  error: string | null
  setRoles: (roles: AssumableRole[]) => void
}) {
  const roles = form.assumable_roles ?? []
  const addRole = () => setRoles([...roles, { group_id: '', label: '', safety_class: 'read-only' }])
  const setRole = (i: number, patch: Partial<AssumableRole>) =>
    setRoles(roles.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const removeRole = (i: number) => setRoles(roles.filter((_, j) => j !== i))

  return (
    <div className="card mb-4">
      <div className="card-title mb-3">Register a target</div>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Name *" placeholder="e.g. prod-us-east"
               value={form.name} onChange={(v) => set('name', v)} />
        <Field label="Workspace name" placeholder="optional label"
               value={form.workspace_name ?? ''} onChange={(v) => set('workspace_name', v)} />
        <Field label="Host" placeholder="https://…cloud.databricks.com"
               value={form.host ?? ''} onChange={(v) => set('host', v)} />
        <div className="flex flex-col gap-1">
          <label className="card-title">Safety class *</label>
          <select className="input" value={form.safety_class}
                  onChange={(e) => set('safety_class', e.target.value as SafetyClass)}>
            {CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <Field label="Secret scope *" placeholder="e.g. validation-platform" mono
               value={form.identity_scope} onChange={(v) => set('identity_scope', v)} />
        <Field label="Secret key *" placeholder="e.g. sp-client-secret" mono
               value={form.identity_key} onChange={(v) => set('identity_key', v)} />
      </div>
      <p className="mt-2 text-xs text-muted">
        Identity is a Databricks secret-scope pair, not the app or workspace name.
        Put the SP client secret at <span className="font-mono text-white">scope/key</span>;
        the platform reads the client id from the sibling secret named{' '}
        <span className="font-mono text-white">{'<key>_client_id'}</span>.
      </p>

      <div className="mt-3">
        <div className="mb-1 flex items-center gap-2">
          <span className="card-title">Assumable roles</span>
          <button className="btn px-2 py-1 text-xs" onClick={addRole} type="button">
            <Plus size="12" /> Add role
          </button>
        </div>
        {roles.length === 0 && (
          <div className="text-xs text-muted">None. A registered role may never out-run the target's class.</div>
        )}
        {roles.map((r, i) => (
          <div key={i} className="mb-2 flex flex-wrap items-end gap-2">
            <Field label="Group id" value={r.group_id} onChange={(v) => setRole(i, { group_id: v })} />
            <Field label="Label" value={r.label} onChange={(v) => setRole(i, { label: v })} />
            <div className="flex flex-col gap-1">
              <label className="card-title">Class</label>
              <select className="input" value={r.safety_class}
                      onChange={(e) => setRole(i, { safety_class: e.target.value as SafetyClass })}>
                {CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <button className="mb-2 text-muted hover:text-fail" onClick={() => removeRole(i)} type="button">
              <X size="15" />
            </button>
          </div>
        ))}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <button className="btn btn-accent" onClick={submit} disabled={pending}>
          <Plus size="16" />
          {pending ? 'Registering…' : 'Register target'}
        </button>
        {error && <div className="text-sm text-fail">{error}</div>}
      </div>
    </div>
  )
}

// --------------------------- Tag filter chips ---------------------------
// Tags are a plain navigation filter over the target list, never a query
// language (CONTEXT.md) — every `key=value` pair across registered targets
// renders as a chip; toggling one narrows the list, and any number can be
// active at once (ANDed) in the same one-click gesture.
function TagFilterBar({
  tags, selected, onToggle, onClear,
}: {
  tags: string[]
  selected: Set<string>
  onToggle: (tag: string) => void
  onClear: () => void
}) {
  return (
    <div className="card mb-4 flex flex-wrap items-center gap-2">
      <span className="card-title">Tags</span>
      {tags.map((tag) => {
        const active = selected.has(tag)
        return (
          <button
            key={tag}
            type="button"
            aria-pressed={active}
            className={`badge transition ${
              active ? 'bg-accent text-ink' : 'bg-ink-soft text-muted hover:text-white'
            }`}
            onClick={() => onToggle(tag)}
          >
            {tag}
          </button>
        )
      })}
      {selected.size > 0 && (
        <button className="ml-1 text-xs text-muted underline hover:text-white" onClick={onClear}>
          Clear ({selected.size})
        </button>
      )}
    </div>
  )
}

function Field({
  label, value, onChange, placeholder, mono,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  mono?: boolean
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="card-title">{label}</label>
      <input className={`input ${mono ? 'font-mono' : ''}`} placeholder={placeholder}
             autoComplete="off" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

// --------------------------- Friction ceremony ---------------------------
// A downgrade widens the blast radius and defeats the safety gate (ADR-0010).
// It is guarded by friction, not permission: escalating warnings + a concrete
// consequence + an AWS-style typed confirmation and a recorded reason.
const CONFIRM_PHRASE = 'i understand'

function DowngradeCeremony({
  target, to, pending, onCancel, onConfirm,
}: {
  target: Target
  to: SafetyClass
  pending: boolean
  onCancel: () => void
  onConfirm: (reason: string) => void
}) {
  const [typed, setTyped] = useState('')
  const [reason, setReason] = useState('')
  const ready = typed.trim().toLowerCase() === CONFIRM_PHRASE && reason.trim().length > 0

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
         role="dialog" aria-modal="true">
      <div className="card w-full max-w-lg border border-fail/40">
        <div className="mb-3 flex items-center gap-2 text-fail">
          <ShieldAlert size="20" />
          <div className="card-title text-fail">Safety-class downgrade</div>
        </div>

        <p className="mb-3 text-sm text-white">
          You are about to widen the blast radius of <b>{target.name}</b> from{' '}
          <ClassBadge value={target.safety_class} /> to <ClassBadge value={to} />.
        </p>

        <div className="mb-3 flex items-start gap-2 rounded border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          <AlertTriangle size="16" className="mt-0.5 shrink-0" />
          <span>
            After this change, the safety gate will allow <b>{CONSEQUENCE[to]}</b> to run against{' '}
            {target.name}. This is not a permission check — it is a deliberate, audited act.
            R3 (catastrophic) operations stay globally forbidden regardless.
          </span>
        </div>

        <label className="card-title">Reason (recorded in the audit log)</label>
        <input className="input mb-3 mt-1 w-full" placeholder="Why is this downgrade necessary?"
               value={reason} onChange={(e) => setReason(e.target.value)} />

        <label className="card-title">
          Type <span className="font-mono text-white">{CONFIRM_PHRASE}</span> to confirm
        </label>
        <input className="input mb-4 mt-1 w-full font-mono" placeholder={CONFIRM_PHRASE}
               value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />

        <div className="flex justify-end gap-2">
          <button className="btn" onClick={onCancel} disabled={pending}>Cancel</button>
          <button className="btn btn-accent disabled:opacity-40"
                  disabled={!ready || pending}
                  onClick={() => onConfirm(reason.trim())}>
            {pending ? 'Downgrading…' : 'Downgrade safety class'}
          </button>
        </div>
      </div>
    </div>
  )
}
