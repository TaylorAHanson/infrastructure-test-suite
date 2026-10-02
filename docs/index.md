---
layout: home
title: Guide

hero:
  name: Validation Platform
  text: Operator guide
  tagline: Regression and connectivity validation for every Databricks workspace you run. Is it connectable? Does it work?
  actions:
    - theme: brand
      text: Install, configure & add tests
      link: /getting-started
    - theme: alt
      text: Day-to-day operations
      link: /operations

features:
  - title: One Run API, three triggers
    details: CI, the GUI, and the nightly Job all call the same POST /runs. Only a PASS verdict passes, so gating is fail-closed.
    link: /ci/
    linkText: Gate a deploy from CI
  - title: Safe by construction
    details: Each Target has a least-privilege service principal and an explicit safety class. R3 (catastrophic) operations are forbidden everywhere.
    link: /glossary#core-vocabulary
    linkText: Safety class & risk tiers
  - title: Tests are short Python scripts
    details: Write each check as a few lines of Python, with the Assistant beside the code. Every call goes through the safety gate, and nothing runs until a human activates it.
    link: /writing-tests
    linkText: Write a test
  - title: Health you can trust after an apply
    details: The bundled Tests read, write and get refused by known canary objects, so a failure means the apply broke something, not that an API was slow.
    link: /canary-fixtures
    linkText: Canary fixtures
  - title: Triage, not dashboards
    details: The "What's Interesting" view shows what regressed since your change and what drifted overnight, with a last-green → first-bad thread for each finding.
    link: /operations#_1-start-the-day-in-what-s-interesting
    linkText: Triage findings
---
