# CLAUDE.md

## Agent skills

### Issue tracker

Issues, specs, and Wayfinder maps live as GitHub issues in `databricks-field-eng/infrastructure-test-suite` (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Code review

`/review` (Isaac Review) is **not needed** in this repo — the owner is not in engineering. Don't prompt to run it before pushing or opening PRs, and don't nudge toward it when another review tool is used.
