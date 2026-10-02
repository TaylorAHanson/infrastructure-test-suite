import { useEffect, useMemo, useState } from 'react'
import { Link, NavLink, useLocation, useParams } from 'react-router-dom'
import { ChevronRight, ExternalLink, Search } from 'lucide-react'

import { NAV_LABELS, NAV_SECTIONS, PAGES, REPO_URL, pageBySlug, plainText, slugify } from '../guide/content'
import type { NavSection } from '../guide/content'
import { GuideMarkdown, headingText, outline } from '../guide/Markdown'

interface Hit {
  slug: string
  title: string
  anchor: string
  snippet: string
}

function search(query: string): Hit[] {
  const q = query.trim().toLowerCase()
  if (q.length < 2) return []
  const hits: Hit[] = []
  for (const page of PAGES) {
    const text = plainText(page.source)
    const at = text.toLowerCase().indexOf(q)
    if (at < 0) continue
    // Land on the section the match is in.
    const before = text.slice(0, at)
    const heading = [...before.matchAll(/^#{2,3}\s+(.+)$/gm)].pop()?.[1]
    const start = Math.max(0, at - 50)
    const snippet = (start ? '…' : '') + text.slice(start, at + q.length + 70).replace(/\s+/g, ' ').trim() + '…'
    hits.push({ slug: page.slug, title: page.title, anchor: heading ? `#${slugify(headingText(heading))}` : '', snippet })
  }
  return hits.slice(0, 12)
}

function SectionNav({ section, current }: { section: NavSection; current: string }) {
  const containsCurrent = section.slugs.includes(current)
  const [open, setOpen] = useState(!section.collapsed || containsCurrent)
  useEffect(() => {
    if (containsCurrent) setOpen(true)
  }, [containsCurrent])

  return (
    <div className="mb-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="card-title flex w-full items-center gap-1 px-2 py-1 text-left hover:text-white"
      >
        {section.title}
        <ChevronRight size="12" className={`ml-auto transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {section.slugs.map((slug) => {
            const page = pageBySlug(slug)
            if (!page) return null
            return (
              <li key={slug}>
                <NavLink
                  to={`/guide/${slug}`}
                  end
                  className={({ isActive }) => `nav-link py-1.5 text-[13px] ${isActive ? 'nav-link-active' : ''}`}
                >
                  {NAV_LABELS[slug] ?? page.title}
                </NavLink>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export default function Guide() {
  const slug = (useParams()['*'] ?? '').replace(/\/+$/, '').replace(/\.md$/, '')
  const { hash } = useLocation()
  const page = pageBySlug(slug)
  const headings = useMemo(() => (page ? outline(page.source) : []), [page])
  const [query, setQuery] = useState('')
  const hits = useMemo(() => search(query), [query])

  useEffect(() => {
    const target = hash ? document.getElementById(decodeURIComponent(hash.slice(1))) : null
    if (target) target.scrollIntoView()
    else document.querySelector('main')?.scrollTo(0, 0)
  }, [slug, hash])

  return (
    <div className="flex gap-6">
      <aside className="sticky top-0 hidden max-h-[calc(100vh-3rem)] w-60 shrink-0 overflow-y-auto md:block">
        <label className="relative mb-4 block">
          <Search size="14" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the guide"
            className="input w-full pl-8"
          />
        </label>
        {query.trim().length >= 2 ? (
          <div className="flex flex-col gap-1">
            {hits.length === 0 && <div className="px-2 text-xs text-muted">No matches.</div>}
            {hits.map((hit) => (
              <Link
                key={hit.slug}
                to={`/guide/${hit.slug}${hit.anchor}`}
                onClick={() => setQuery('')}
                className="rounded-lg px-2 py-1.5 hover:bg-ink-soft"
              >
                <div className="text-[13px] text-white">{hit.title}</div>
                <div className="line-clamp-2 text-[11px] text-muted">{hit.snippet}</div>
              </Link>
            ))}
          </div>
        ) : (
          NAV_SECTIONS.map((section) => <SectionNav key={section.title} section={section} current={slug} />)
        )}
      </aside>

      <article className="min-w-0 max-w-3xl flex-1">
        {page ? (
          <>
            <GuideMarkdown source={page.source} file={page.file} />
            <a
              href={`${REPO_URL}/edit/main/${page.file}`}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-10 inline-flex items-center gap-1 border-t border-edge pt-4 text-xs text-muted hover:text-white"
            >
              Edit this page on GitHub <ExternalLink size="12" />
            </a>
          </>
        ) : (
          <div className="card">
            <div className="text-sm text-white">There's no guide page at “{slug}”.</div>
            <Link to="/guide" className="mt-2 inline-block text-sm text-accent hover:underline">
              Go to the guide's overview
            </Link>
          </div>
        )}
      </article>

      {headings.length > 1 && (
        <nav aria-label="On this page" className="sticky top-0 hidden max-h-[calc(100vh-3rem)] w-52 shrink-0 overflow-y-auto xl:block">
          <div className="card-title mb-2">On this page</div>
          <ul className="flex flex-col gap-1 border-l border-edge">
            {headings.map((h) => (
              <li key={h.id}>
                <a
                  href={`#${h.id}`}
                  className={`-ml-px block border-l py-0.5 text-xs hover:text-white ${
                    hash === `#${h.id}` ? 'border-accent text-white' : 'border-transparent text-muted'
                  } ${h.depth === 3 ? 'pl-6' : 'pl-3'}`}
                >
                  {h.text}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </div>
  )
}
