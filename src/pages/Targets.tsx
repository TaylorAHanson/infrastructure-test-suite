import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'

import { PageHeader } from '../components/PageHeader'
import {
  useCatalogs,
  useCreateTarget,
  useDeleteTarget,
  useTargets,
  type CreateTargetBody,
} from '../hooks/queries'

const EMPTY: CreateTargetBody = {
  name: '',
  target_type: 'workspace',
  catalog: '',
  schema_name: '',
  workspace_name: '',
  cloud: 'aws',
  region: '',
}

export default function Targets() {
  const { data: targets } = useTargets()
  const { data: catalogs } = useCatalogs()
  const create = useCreateTarget()
  const del = useDeleteTarget()

  const [form, setForm] = useState<CreateTargetBody>(EMPTY)
  const [error, setError] = useState<string | null>(null)

  const isCatalog = form.target_type === 'catalog'

  function set<K extends keyof CreateTargetBody>(key: K, value: CreateTargetBody[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function submit() {
    setError(null)
    if (!form.name.trim()) {
      setError('Name is required.')
      return
    }
    if (isCatalog && !form.catalog?.trim()) {
      setError('Catalog name is required for a catalog target.')
      return
    }
    // Catalog probes read catalog/schema from target.config.
    const config: Record<string, unknown> = {}
    if (form.catalog?.trim()) config.catalog = form.catalog.trim()
    if (form.schema_name?.trim()) config.schema = form.schema_name.trim()
    try {
      await create.mutateAsync({
        name: form.name.trim(),
        target_type: form.target_type,
        catalog: isCatalog ? form.catalog?.trim() || null : null,
        schema_name: form.schema_name?.trim() || null,
        workspace_name: form.workspace_name?.trim() || null,
        cloud: form.cloud || 'aws',
        region: form.region?.trim() || null,
        config,
      })
      setForm(EMPTY)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create target.')
    }
  }

  return (
    <div>
      <PageHeader title="Targets & Catalogs" subtitle="Environments probes run against; drives all/partial catalog execution" />

      <div className="card mb-4">
        <div className="card-title mb-3">Add a target</div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="card-title">Type</label>
            <select className="input" value={form.target_type} onChange={(e) => set('target_type', e.target.value)}>
              <option value="workspace">Workspace</option>
              <option value="catalog">Catalog</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="card-title">Name *</label>
            <input
              className="input"
              placeholder={isCatalog ? 'e.g. sales_dev' : 'e.g. prod workspace'}
              value={form.name}
              onChange={(e) => set('name', e.target.value)}
            />
          </div>
          {isCatalog && (
            <>
              <div className="flex flex-col gap-1">
                <label className="card-title">Catalog *</label>
                <input className="input" placeholder="unity catalog name" value={form.catalog ?? ''}
                       onChange={(e) => set('catalog', e.target.value)} />
              </div>
              <div className="flex flex-col gap-1">
                <label className="card-title">Schema</label>
                <input className="input" placeholder="optional" value={form.schema_name ?? ''}
                       onChange={(e) => set('schema_name', e.target.value)} />
              </div>
            </>
          )}
          <div className="flex flex-col gap-1">
            <label className="card-title">Workspace name</label>
            <input className="input" placeholder="optional label" value={form.workspace_name ?? ''}
                   onChange={(e) => set('workspace_name', e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="card-title">Cloud</label>
            <select className="input" value={form.cloud} onChange={(e) => set('cloud', e.target.value)}>
              <option value="aws">aws</option>
              <option value="azure">azure</option>
              <option value="gcp">gcp</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="card-title">Region</label>
            <input className="input" placeholder="e.g. us-east-1" value={form.region ?? ''}
                   onChange={(e) => set('region', e.target.value)} />
          </div>
          <button className="btn btn-accent" onClick={submit} disabled={create.isPending}>
            <Plus size="16" />
            {create.isPending ? 'Adding...' : 'Add target'}
          </button>
        </div>
        {error && <div className="mt-2 text-sm text-fail">{error}</div>}
      </div>

      <div className="card mb-4">
        <div className="card-title mb-2">Catalogs</div>
        <div className="flex flex-wrap gap-2">
          {catalogs?.map((c) => <span key={c} className="badge bg-ink-soft text-info">{c}</span>)}
          {!catalogs?.length && <span className="text-sm text-muted">No catalogs configured. Add a catalog target above.</span>}
        </div>
      </div>

      <div className="card overflow-hidden p-0">
        <table className="w-full">
          <thead className="border-b border-edge bg-ink">
            <tr>
              <th className="th">Name</th>
              <th className="th">Type</th>
              <th className="th">Catalog</th>
              <th className="th">Schema</th>
              <th className="th">Workspace</th>
              <th className="th">Cloud / Region</th>
              <th className="th"></th>
            </tr>
          </thead>
          <tbody>
            {targets?.map((t) => (
              <tr key={t.id} className="border-b border-edge/50 hover:bg-ink-soft">
                <td className="td text-white">{t.name}</td>
                <td className="td text-muted">{t.target_type}</td>
                <td className="td text-muted">{t.catalog ?? '-'}</td>
                <td className="td text-muted">{t.schema_name ?? '-'}</td>
                <td className="td text-muted">{t.workspace_name ?? '-'}</td>
                <td className="td text-muted">{t.cloud} / {t.region ?? '-'}</td>
                <td className="td text-right">
                  <button
                    className="text-muted hover:text-fail"
                    title="Delete target"
                    onClick={() => del.mutate(t.id)}
                    disabled={del.isPending}
                  >
                    <Trash2 size="15" />
                  </button>
                </td>
              </tr>
            ))}
            {!targets?.length && (
              <tr>
                <td className="td text-sm text-muted" colSpan={7}>No targets yet. Add one above.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
