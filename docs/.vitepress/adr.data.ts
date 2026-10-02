import { createContentLoader } from 'vitepress'

export interface Adr {
  url: string
  title: string
  status: string
}

declare const data: Adr[]
export { data }

// Every ADR under docs/adr/, in number order, for the decisions index page.
// Title comes from the `# ADR NNNN — …` heading, status from the `**Status:**` line.
export default createContentLoader('adr/*.md', {
  includeSrc: true,
  transform(raw): Adr[] {
    return raw
      .map(({ url, src = '' }) => ({
        url,
        title: src.match(/^#\s+(.+)$/m)?.[1].trim() ?? url,
        status: src.match(/\*\*Status:\*\*\s*([^.(\n]+)/)?.[1].trim() ?? '',
      }))
      .sort((a, b) => a.url.localeCompare(b.url))
  },
})
