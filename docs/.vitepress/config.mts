import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type DefaultTheme } from 'vitepress'
import type MarkdownIt from 'markdown-it'

// The operator guide: a VitePress site whose source is this `docs/` directory.
// It builds into backend/static/guide (after `vite build` has emptied
// backend/static) and the FastAPI process serves it at /guide — see
// backend/app/main.py. Agent-facing docs, HTML prototypes and research notes are excluded.

const DOCS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const REPO_URL = 'https://github.com/databricks-field-eng/infrastructure-test-suite'

// Repo-relative paths that have a page in this site (everything else that a
// doc links to — databricks.yml, infra/sql, … — is sent to GitHub instead).
const PAGE_ALIASES: Record<string, string> = {
  'CONTEXT.md': '/glossary.md',
  'docs/adr': '/decisions.md',
}

function adrSidebar(): DefaultTheme.SidebarItem[] {
  return fs
    .readdirSync(path.join(DOCS_DIR, 'adr'))
    .filter((f) => /^\d{4}-.*\.md$/.test(f))
    .sort()
    .map((f) => {
      const heading = fs.readFileSync(path.join(DOCS_DIR, 'adr', f), 'utf8').match(/^#\s+(.+)$/m)
      return { text: heading ? heading[1].trim() : f, link: `/adr/${f.replace(/\.md$/, '')}` }
    })
}

/**
 * Rewrite relative links that leave the site (`../databricks.yml`,
 * `./example-github-action.yml`, `adr/`) so the existing repo docs render
 * unchanged: known aliases become site pages, other repo paths become GitHub
 * links. Links to pages inside the site are left for VitePress to resolve.
 */
function repoLinks(md: MarkdownIt) {
  md.core.ruler.push('repo_links', (state) => {
    const relativePath: string = state.env.relativePath ?? ''
    for (const block of state.tokens) {
      for (const token of block.children ?? []) {
        if (token.type !== 'link_open') continue
        const href = token.attrGet('href')
        if (!href || /^([a-z]+:|\/|#)/i.test(href)) continue
        const [target, hash] = href.split('#')
        const repoPath = path.posix.normalize(
          path.posix.join('docs', path.posix.dirname(relativePath), target),
        ).replace(/\/$/, '')
        const suffix = hash ? `#${hash}` : ''
        if (PAGE_ALIASES[repoPath]) {
          token.attrSet('href', PAGE_ALIASES[repoPath] + suffix)
        } else if (!(repoPath.startsWith('docs/') && repoPath.endsWith('.md'))) {
          const kind = path.posix.extname(repoPath) ? 'blob' : 'tree'
          token.attrSet('href', `${REPO_URL}/${kind}/main/${repoPath}${suffix}`)
        }
      }
    }
  })
}

export default defineConfig({
  title: 'Validation Platform',
  description: 'How to install, configure, and operate the Databricks regression & connectivity validation platform.',
  base: '/guide/',
  outDir: '../backend/static/guide',
  srcExclude: ['agents/**', 'prototypes/**', 'research/**'],
  rewrites: { 'ci/README.md': 'ci/index.md' },
  lastUpdated: true,
  markdown: { config: repoLinks },
  themeConfig: {
    search: { provider: 'local' },
    outline: [2, 3],
    nav: [
      { text: 'Setup', link: '/getting-started' },
      { text: 'Writing tests', link: '/writing-tests' },
      { text: 'Operations', link: '/operations' },
      { text: 'Glossary', link: '/glossary' },
    ],
    sidebar: [
      {
        text: 'Quickstarts',
        items: [
          { text: 'Install, configure & add tests', link: '/getting-started' },
          { text: 'Writing tests', link: '/writing-tests' },
          { text: 'Canary fixtures', link: '/canary-fixtures' },
          { text: 'Day-to-day operations', link: '/operations' },
        ],
      },
      {
        text: 'Reference',
        items: [
          { text: 'Glossary', link: '/glossary' },
          { text: 'Architecture', link: '/architecture' },
          { text: 'Deployment', link: '/deployment' },
          { text: 'Gating a deploy from CI', link: '/ci/' },
          { text: 'PrivateLink / NCC troubleshooting', link: '/privatelink-ncc-troubleshooting' },
        ],
      },
      {
        text: 'Design decisions (ADRs)',
        collapsed: true,
        items: [{ text: 'Index', link: '/decisions' }, ...adrSidebar()],
      },
    ],
    editLink: {
      pattern: `${REPO_URL}/edit/main/docs/:path`,
      text: 'Edit this page on GitHub',
    },
    socialLinks: [{ icon: 'github', link: REPO_URL }],
  },
})
