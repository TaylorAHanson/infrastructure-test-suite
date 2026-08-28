# ADR 0011 — Target-level run locking

- **Status:** Accepted (2026-08-28). Resolves [#14](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/14). **Refines [ADR-0005](./0005-ci-invocation-contract.md) §7** (which allowed unconditional same-target concurrency).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1).

## Context

ADR-0007 made in-app run concurrency a single global setting but left **collision control** as fog (its "per-suite serialize flag"). ADR-0005 §7 currently *allows* concurrent runs against the same target, reasoning that "the safety gate already caps mutation." But the safety gate (ADR-0004) caps *how destructive* an operation may be — it does **not** stop two permitted mutating runs from trampling each other's state on the same target and producing **untrustworthy verdicts**, which is the exact failure the platform exists to prevent. The collision is not per-*suite* — two different suites hitting the same target collide just as badly — so it must be decided at the **target** level. Constraints: SIMPLIFY (cost is a non-goal, ADR-0007 §6), zero-training, and the OSS litmus (no external infrastructure a stranger must stand up).

## Decision

1. **Lock is per-target, gated by safety class.** A `read-only` target (max R0, ADR-0004) has no mutation to interfere, so it **never locks** — concurrent runs are allowed, preserving the fast ~100-target read-only CI path. `prod-for-us` and `sandbox` targets **serialize**: at most one active Run per target. Keying on safety class reuses the authoritative concept the user already knows (ADR-0002) and locks exactly where interference is possible.
2. **Collision → queue, never refuse.** A Run selecting a locked target enters a per-target **FIFO queue**; it is a non-terminal **`queued`** phase of the run lifecycle, **not a Verdict**. Refusing was rejected: a non-`pass` refusal would block a legitimate deploy merely because another run was in flight, a `pass` refusal would silently skip validation, and either forces new caller-side interpretation logic (violates zero-training).
3. **The CI contract is untouched.** A `queued` run is simply "not terminal yet" under the existing async poll contract (ADR-0005 §2); the caller already owns its timeout, so the extra wait is already handled. The fail-closed mapping (ADR-0005 §5) is **unchanged** — the only terminal states remain `pass` / `fail` / `BLOCKED_BY_SAFETY` / `error` / `timeout`. The lock is **invisible** to the CI contract; nothing new to learn.
4. **The lock lives in Lakebase** (the authoritative store, ADR-0001) as a per-target **lease**. In-memory locking is rejected: runs execute in-app and may span replicas (ADR-0007 §4), so only a shared, authoritative lock is correct. **No external lock service** (Redis/ZooKeeper) — keeps the OSS story clean.
5. **Crash/wedge release via a lease TTL.** The holding run renews its lease by **heartbeat** while alive; a lapsed lease is reclaimable by the next queued run. A **server-side max run duration** — distinct from the caller-owned timeout (ADR-0005 §2) — force-terminates a wedged run and releases its lease, so a dead holder can never deadlock a target forever.
6. **Queue depth is unbounded FIFO for now.** Cost is a non-goal (ADR-0007 §6); a max-queue-depth or per-run deadline for bursty CI is left as **fog**, not decided here.

## Consequences

- **+** Verdicts on mutating targets are trustworthy — no cross-run state trampling; the platform's core promise holds.
- **+** The CI contract and fail-closed gating are preserved **by construction** — the lock never surfaces as a new outcome a caller must handle.
- **+** `read-only` CI stays fully concurrent across ~100 targets; the lock costs nothing on the common path.
- **+** Lakebase-only, no external lock infrastructure — a stranger can run it with no extra services.
- **−** A long run on a mutating target serializes everything behind it; bursty CI can build a queue (bounded only by deploy rate until the fog item is decided).
- **−** Introduces a **server-side max-run-duration** cap that must be tuned: too low kills legitimate long `full` runs, too high slows lease reclaim after a crash.
- **−** A caller that times out and stops polling still leaves its queued run to execute later (minor wasted work; accepted — cost is a non-goal).

## Downstream

Refines ADR-0005 §7. Consumes ADR-0001 (Lakebase authoritative; the lease is a Lakebase row), ADR-0002/ADR-0004 (safety class gates whether a target locks), and ADR-0007 (in-app concurrency — the global concurrency setting bounds parallelism *across* targets; this lease bounds it *per* target). The "what's interesting" view ([#10](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/10), ADR-0009) may surface the `queued` phase. A max-queue-depth / per-run deadline for bursty CI remains fog.
