import { Fragment, useState } from 'react'
import { format, parseISO } from 'date-fns'

import { PageHeader } from '../components/PageHeader'
import { useReleaseNotes } from '../hooks/queries'

// Release notes ship in the repo as RELEASE_NOTES.md and are served read-only —
// they describe the code, so a deploy is the only way they change. The backend
// already sorts newest first; the only markup a note may carry is **bold** and
// `code`, so that's all this renders.
function inlineMarkup(text: string) {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 1)
      return <code key={i} className="rounded bg-ink px-1 font-mono text-[0.85em] text-info">{part.slice(1, -1)}</code>
    if (part.startsWith('**') && part.endsWith('**') && part.length > 3)
      return <strong key={i} className="font-semibold text-white">{part.slice(2, -2)}</strong>
    return <Fragment key={i}>{part}</Fragment>
  })
}

export default function ReleaseNotes() {
  const { data, isError } = useReleaseNotes()
  const releases = data?.releases ?? []
  const [selected, setSelected] = useState<string | null>(null)
  const release = releases.find((r) => r.version === selected) ?? releases[0]

  return (
    <div>
      <PageHeader title="Release notes" subtitle="What changed in each release, newest first." />
      <div className="card">
        {isError && <div className="text-sm text-warn">Couldn't load release notes.</div>}
        {release && (
          <div className="flex flex-col gap-4 md:flex-row">
            <nav aria-label="Releases" className="shrink-0 md:w-44">
              <ul className="flex gap-1 overflow-x-auto md:flex-col">
                {releases.map((r) => (
                  <li key={r.version}>
                    <button
                      type="button"
                      onClick={() => setSelected(r.version)}
                      aria-current={r.version === release.version ? 'true' : undefined}
                      className={`nav-link w-full whitespace-nowrap py-1.5 ${r.version === release.version ? 'nav-link-active' : ''}`}
                    >
                      <span className="font-mono">v{r.version}</span>
                      {r.version === data?.current_version && (
                        <span className="badge ml-auto bg-pass/15 text-pass">current</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>

            <article className="min-w-0 flex-1 border-edge md:border-l md:pl-5">
              <h2 className="text-lg font-semibold text-white">
                v{release.version}
                <span className="ml-2 text-sm font-normal text-muted">
                  {format(parseISO(release.date), 'MMMM d, yyyy')}
                </span>
              </h2>
              {release.summary && (
                <p className="mt-1 max-w-3xl text-sm text-gray-300">{inlineMarkup(release.summary)}</p>
              )}
              {release.sections.map((section) => (
                <section key={section.title} className="mt-4">
                  <h3 className="mb-1.5 text-sm font-semibold text-white">{section.title}</h3>
                  <ul className="max-w-3xl list-disc space-y-1 pl-5 text-sm text-gray-300 marker:text-muted">
                    {section.items.map((item, i) => (
                      <li key={i}>{inlineMarkup(item)}</li>
                    ))}
                  </ul>
                </section>
              ))}
            </article>
          </div>
        )}
      </div>
    </div>
  )
}
