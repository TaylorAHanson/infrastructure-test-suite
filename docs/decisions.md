# Design decisions

Each architecture decision record (ADR) explains *why* one part of the platform
works the way it does: the options considered, what was chosen, and what it
costs. The quickstarts tell you what to do. Read an ADR when you want to know why
the platform behaves a certain way, for example why the safety class is never
guessed from a workspace's name or why `read-only` Targets never lock.

Later ADRs can amend earlier ones. When two disagree, the later one wins, and the
[glossary](./glossary.md) always reflects the current decision. ADRs are a
record, so they aren't rewritten when a feature goes away: several describe the
structured Checks, YAML export, worked-example library and LLM judge that Python
script Tests replaced in v1.1.0. The glossary's
[retired terms](./glossary.md#retired-terms) list them.

<!-- adr-index -->
