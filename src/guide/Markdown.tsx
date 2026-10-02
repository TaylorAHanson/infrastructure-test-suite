import { useMemo } from 'react'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link } from 'react-router-dom'
import { AlertTriangle, Info, Lightbulb } from 'lucide-react'

import { resolveLink, slugify } from './content'

type Segment = { kind: 'md'; text: string } | { kind: 'callout'; type: string; title: string; text: string }

const CALLOUT_OPEN = /^:::\s*(tip|info|note|warning|danger)\b\s*(.*)$/
const CALLOUT_TITLES: Record<string, string> = { tip: 'Tip', info: 'Info', note: 'Note', warning: 'Warning', danger: 'Danger' }

/** Split out `::: tip Title … :::` callouts; everything else is plain markdown. */
export function segments(source: string): Segment[] {
  const text = source.replace(/^---\n[\s\S]*?\n---\n/, '').replace(/<!--[\s\S]*?-->/g, '')
  const out: Segment[] = []
  let md: string[] = []
  let callout: { type: string; title: string; lines: string[] } | null = null
  let fenced = false
  for (const line of text.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced
    const opener: RegExpMatchArray | null = !fenced && !callout ? line.match(CALLOUT_OPEN) : null
    if (opener) {
      out.push({ kind: 'md', text: md.join('\n') })
      md = []
      callout = { type: opener[1], title: opener[2].trim() || CALLOUT_TITLES[opener[1]], lines: [] }
    } else if (callout && !fenced && /^:::\s*$/.test(line)) {
      out.push({ kind: 'callout', type: callout.type, title: callout.title, text: callout.lines.join('\n') })
      callout = null
    } else if (callout) {
      callout.lines.push(line)
    } else {
      md.push(line)
    }
  }
  if (callout) md.push(...callout.lines)
  out.push({ kind: 'md', text: md.join('\n') })
  return out.filter((s) => s.kind === 'callout' || s.text.trim())
}

interface HastNode {
  type: string
  value?: string
  children?: HastNode[]
}

function textOf(node: HastNode | undefined): string {
  if (!node) return ''
  if (node.type === 'text') return node.value ?? ''
  return (node.children ?? []).map(textOf).join('')
}

export interface Heading {
  depth: number
  text: string
  id: string
}

/** A markdown heading's visible text (what its anchor is derived from). */
export function headingText(markdown: string): string {
  return markdown
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*|\*([^*]+)\*/g, '$1$2')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
}

/** The page's h2/h3 headings, for the "On this page" outline. */
export function outline(source: string): Heading[] {
  const out: Heading[] = []
  let fenced = false
  for (const line of source.replace(/<!--[\s\S]*?-->/g, '').split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced
    const m = !fenced && line.match(/^(#{1,6})\s+(.+?)\s*#*$/)
    if (!m) continue
    const text = headingText(m[2])
    const id = slugify(text)
    if (m[1].length === 2 || m[1].length === 3) out.push({ depth: m[1].length, text, id })
  }
  return out
}

const CALLOUT_STYLE: Record<string, { box: string; icon: ReactNode }> = {
  tip: { box: 'border-pass/40 bg-pass/10', icon: <Lightbulb size="14" className="text-pass" /> },
  info: { box: 'border-info/40 bg-info/10', icon: <Info size="14" className="text-info" /> },
  note: { box: 'border-info/40 bg-info/10', icon: <Info size="14" className="text-info" /> },
  warning: { box: 'border-warn/40 bg-warn/10', icon: <AlertTriangle size="14" className="text-warn" /> },
  danger: { box: 'border-fail/40 bg-fail/10', icon: <AlertTriangle size="14" className="text-fail" /> },
}

export function GuideMarkdown({ source, file }: { source: string; file: string }) {
  const parts = useMemo(() => segments(source), [source])

  // A heading's id is its slug alone (tests/test_guide_links.py keeps headings
  // unique per page), so it matches outline() and links from other pages.
  const heading =
    (Tag: 'h1' | 'h2' | 'h3' | 'h4') =>
    ({ node, children }: { node?: unknown; children?: ReactNode }) => {
      const id = slugify(textOf(node as HastNode))
      return (
        <Tag id={id} className="group scroll-mt-4">
          {children}
          {Tag !== 'h1' && (
            <a href={`#${id}`} aria-label="Link to this section" className="ml-2 text-muted opacity-0 group-hover:opacity-100">
              #
            </a>
          )}
        </Tag>
      )
    }

  const components: Components = {
    h1: heading('h1'),
    h2: heading('h2'),
    h3: heading('h3'),
    h4: heading('h4'),
    a: ({ href = '', children }) => {
      const link = resolveLink(href, file)
      if (link.kind === 'external')
        return (
          <a href={link.href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        )
      if (link.to.startsWith('#')) return <a href={link.to}>{children}</a>
      return <Link to={link.to}>{children}</Link>
    },
    table: ({ children }) => (
      <div className="overflow-x-auto">
        <table>{children}</table>
      </div>
    ),
  }

  return (
    <div className="guide-prose">
      {parts.map((part, i) =>
        part.kind === 'md' ? (
          <ReactMarkdown key={i} remarkPlugins={[remarkGfm]} components={components}>
            {part.text}
          </ReactMarkdown>
        ) : (
          <div key={i} className={`my-4 rounded-lg border px-4 py-3 ${CALLOUT_STYLE[part.type].box}`}>
            <div className="mb-1 flex items-center gap-2 text-sm font-semibold text-white">
              {CALLOUT_STYLE[part.type].icon}
              {part.title}
            </div>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
              {part.text}
            </ReactMarkdown>
          </div>
        ),
      )}
    </div>
  )
}
