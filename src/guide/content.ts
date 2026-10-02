// The operator guide's pages: the repo's own markdown (docs/ plus CONTEXT.md as
// the glossary), bundled at build time so the guide ships with the app and is
// always the docs for the code that's running. Agent-facing docs, prototypes and
// research notes aren't part of it.
import context from '../../CONTEXT.md?raw'

const raw = import.meta.glob(['../../docs/*.md', '../../docs/ci/*.md', '../../docs/adr/*.md'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

export const REPO_URL = 'https://github.com/databricks-field-eng/infrastructure-test-suite'

export interface GuidePage {
  /** Route under /guide/ ('' is the guide's home). */
  slug: string
  /** Repo-relative source path, e.g. docs/operations.md. */
  file: string
  title: string
  source: string
}

export interface Adr {
  slug: string
  title: string
  status: string
}

function slugFor(file: string): string {
  if (file === 'CONTEXT.md') return 'glossary'
  const rel = file.replace(/^docs\//, '').replace(/\.md$/, '')
  if (rel === 'index') return ''
  return rel.replace(/(^|\/)README$/, '')
}

function titleOf(source: string, fallback: string): string {
  return source.match(/^#\s+(.+)$/m)?.[1].trim() ?? fallback
}

const files: Record<string, string> = { 'CONTEXT.md': context }
for (const [key, text] of Object.entries(raw)) {
  const file = key.replace(/^(\.\.\/)+/, '')
  // docs/glossary.md is only a pointer to CONTEXT.md for people browsing the repo.
  if (file !== 'docs/glossary.md') files[file] = text
}

export const ADRS: Adr[] = Object.entries(files)
  .filter(([file]) => /^docs\/adr\/\d{4}-/.test(file))
  .sort(([a], [b]) => a.localeCompare(b))
  .map(([file, source]) => ({
    slug: slugFor(file),
    title: titleOf(source, file),
    status: source.match(/\*\*Status:\*\*\s*([^.(\n]+)/)?.[1].trim() ?? '',
  }))

function adrIndex(): string {
  const rows = ADRS.map((a) => `| [${a.title.replace(/\|/g, '\\|')}](/guide/${a.slug}) | ${a.status} |`)
  return ['| Decision | Status |', '| --- | --- |', ...rows].join('\n')
}

export const PAGES: GuidePage[] = Object.entries(files).map(([file, text]) => {
  const source = file === 'docs/decisions.md' ? text.replace('<!-- adr-index -->', adrIndex()) : text
  return { slug: slugFor(file), file, title: titleOf(source, file), source }
})

const bySlug = new Map(PAGES.map((p) => [p.slug, p]))
const byFile = new Map(PAGES.map((p) => [p.file, p]))

export const pageBySlug = (slug: string) => bySlug.get(slug)
export const pageByFile = (file: string) => byFile.get(file)

export interface NavSection {
  title: string
  slugs: string[]
  collapsed?: boolean
}

export const NAV_SECTIONS: NavSection[] = [
  {
    title: 'Quickstarts',
    slugs: ['', 'getting-started', 'writing-tests', 'canary-fixtures', 'operations'],
  },
  {
    title: 'Reference',
    slugs: ['glossary', 'architecture', 'deployment', 'ci', 'privatelink-ncc-troubleshooting'],
  },
  {
    title: 'Design decisions (ADRs)',
    slugs: ['decisions', ...ADRS.map((a) => a.slug)],
    collapsed: true,
  },
]

/** Short sidebar labels; anything not listed uses the page's own title. */
export const NAV_LABELS: Record<string, string> = {
  '': 'Overview',
  'getting-started': 'Install, configure & add tests',
  operations: 'Day-to-day operations',
  glossary: 'Glossary',
  ci: 'Gating a deploy from CI',
  'privatelink-ncc-troubleshooting': 'PrivateLink / NCC troubleshooting',
  decisions: 'Index',
}

// VitePress's heading slugs, kept so the anchors the docs already link to
// (`#_5-provision-the-canary-fixtures`) still resolve.
const rSpecial = /[\s~`!@#$%^&*()\-_+=[\]{}|\\;:"'“”‘’<>,.?/]+/g
export function slugify(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/[\u0300-\u036F]/g, '')
    // Stripping control characters is the point of this one.
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u001f]/g, '')
    .replace(rSpecial, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^-+|-+$/g, '')
    .replace(/^(\d)/, '_$1')
    .toLowerCase()
}

function normalize(path: string): string {
  const out: string[] = []
  for (const part of path.split('/')) {
    if (part === '..') out.pop()
    else if (part && part !== '.') out.push(part)
  }
  return out.join('/')
}

export type ResolvedLink = { kind: 'internal'; to: string } | { kind: 'external'; href: string }

/**
 * Where a link in `fromFile` points. Links between docs become guide routes;
 * links to other repo files (databricks.yml, infra/sql, …) go to GitHub.
 */
export function resolveLink(href: string, fromFile: string): ResolvedLink {
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return { kind: 'external', href }
  if (href.startsWith('#')) return { kind: 'internal', to: href }
  if (href.startsWith('/')) return { kind: 'internal', to: href }

  const [target, hash] = href.split('#')
  const suffix = hash ? `#${hash}` : ''
  const dir = fromFile.includes('/') ? fromFile.slice(0, fromFile.lastIndexOf('/')) : ''
  const repoPath = normalize(`${dir}/${target}`)

  if (repoPath === 'docs/adr') return { kind: 'internal', to: `/guide/decisions${suffix}` }
  if (repoPath === 'docs/glossary.md') return { kind: 'internal', to: `/guide/glossary${suffix}` }
  const page = pageByFile(repoPath) ?? pageByFile(`${repoPath}/README.md`) ?? pageByFile(`${repoPath}/index.md`)
  if (page) return { kind: 'internal', to: `/guide/${page.slug}${suffix}` }

  const kind = /\.[a-z0-9]+$/i.test(repoPath) ? 'blob' : 'tree'
  return { kind: 'external', href: `${REPO_URL}/${kind}/main/${repoPath}${suffix}` }
}

/** The page's text with frontmatter, HTML comments and fenced code removed, for search. */
export function plainText(source: string): string {
  return source
    .replace(/^---\n[\s\S]*?\n---\n/, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/```[\s\S]*?```/g, ' ')
}
