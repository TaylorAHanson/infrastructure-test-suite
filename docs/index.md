# Operator guide

Regression and connectivity validation for every Databricks workspace you run.
Two questions, after every change: **is it connectable, and does it work?**

New here? Start with [Install, configure & add tests](./getting-started.md). If
the platform is already running, [Day-to-day operations](./operations.md) covers
the daily loop.

## What the platform does

**One Run API, three triggers.** CI, the app, and the nightly Job all call the
same `POST /runs`. Only a PASS verdict passes, so gating is fail-closed.
[Gate a deploy from CI](./ci/README.md).

**Safe by construction.** Each Target has a least-privilege service principal and
an explicit safety class. R3 (catastrophic) operations are forbidden everywhere.
[Safety class & risk tiers](./glossary.md#core-vocabulary).

**Tests are short Python scripts.** Write each check as a few lines of Python,
with the Assistant beside the code. Every call goes through the safety gate, and
nothing runs until a human activates it. [Write a test](./writing-tests.md).

**Health you can trust after an apply.** The bundled Tests read, write and get
refused by known canary objects, so a failure means the apply broke something,
not that an API was slow. [Canary fixtures](./canary-fixtures.md).

**Triage, not dashboards.** The What's Interesting view shows what regressed since
your change and what drifted overnight, with a last-green → first-bad thread for
each finding. [Triage findings](./operations.md#_1-start-the-day-in-what-s-interesting).

## Where to look

| You want to… | Read |
| --- | --- |
| Stand the platform up and register a workspace | [Install, configure & add tests](./getting-started.md) |
| Write or edit a Test | [Writing tests](./writing-tests.md) |
| Provision the objects the health Tests use | [Canary fixtures](./canary-fixtures.md) |
| Triage, run on demand, manage Targets and Tests | [Day-to-day operations](./operations.md) |
| Look up a term (Check, Target, Depth, Verdict…) | [Glossary](./glossary.md) |
| Know why something works the way it does | [Design decisions](./decisions.md) |
