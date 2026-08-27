# ADR 0007 — Suite + run-depth model

- **Status:** Accepted (2026-08-27). Resolves [#9](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/9).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1).

## Context

CI runs must be fast; nightly runs can be thorough. The destination requires an **adaptable depth attribute, not fixed PREFLIGHT/SMOKE/FULL names**. Earlier drafting of this ticket over-reached (configurable depth scales, per-check cost positions, orchestration strategies, cost-budget primitives); the steer was to **keep it minimal — cost is a non-goal**.

## Decision

1. **Suite is unchanged** — a named selection of Tests + target(s). No new structure added here.
2. **One coarse depth dial on a Run: `quick` vs `full`.** `quick` runs the connectivity/read-only end of the stage spine (fast); `full` runs everything through `OPERATE`/`VERIFY`. One dial, one run, one verdict — **no** two-phase early-verdict engine.
3. **Default is `quick`, never `full`.** The CI trigger calls the same Run API (ADR-0005), so a `full` run must be an **explicit opt-in** — an unspecified run is always the cheap/fast one. Nightly opts into `full`.
4. **Runs execute in-app, concurrently** (I/O-bound API calls to targets) — **no per-target/-run Databricks Job fan-out.**
5. **Concurrency is a configurable setting** — a bounded max-parallelism limit on in-app fan-out across targets/checks, exposed as a platform setting (anticipating rate-limit / thundering-herd issues at ~100 targets).
6. **Cost accounting is out.** No budget primitive, no $ cap; the dial and the concurrency setting are latency/scope controls, not cost controls.
7. The dial's *levels stay adaptable* (destination requirement) — we ship exactly `quick`/`full` and leave room to add levels later without renaming.

## Consequences

- **+** Dead-simple to explain (OSS litmus): two levels, one setting, safe-by-default (`quick`).
- **+** CI can't accidentally trigger an expensive `full` run — it must ask for it.
- **+** No cluster-spin cost or latency from job fan-out; the concurrency knob is the single lever for parallelism trouble.
- **−** If a `full` run ever exceeds a Databricks App's runtime limits, dispatching it to a serverless job is an unbuilt escape hatch (left as fog).
- **−** Only two depth levels ship; finer gradation waits until a real need appears.

## Downstream

Consumes the result model (ADR-0001, depth rides the stage spine) and the Run API (ADR-0005, depth is a run parameter). The "what's interesting" dashboard ([#10](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/10)) reads the same verdicts regardless of depth. A per-suite **serialize** flag for unsafe concurrent overlap remains fog.
