---
title: Design decisions
---

<script setup>
import { withBase } from 'vitepress'
import { data as adrs } from './.vitepress/adr.data'
</script>

# Design decisions

Each architecture decision record (ADR) explains *why* one part of the platform
works the way it does: the options considered, what was chosen, and what it
costs. The quickstarts tell you what to do. Read an ADR when you want to know why
the platform behaves a certain way, for example why `agentic` Checks never gate
CI or why `read-only` Targets never lock.

Later ADRs can amend earlier ones. When two disagree, the later one wins, and the
[glossary](./glossary.md) always reflects the current decision.

<table>
  <thead><tr><th>Decision</th><th>Status</th></tr></thead>
  <tbody>
    <tr v-for="adr in adrs" :key="adr.url">
      <td><a :href="withBase(adr.url)">{{ adr.title }}</a></td>
      <td>{{ adr.status }}</td>
    </tr>
  </tbody>
</table>
