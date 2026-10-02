# Backend Inventory: Current State vs. New Design Goals

## Summary

The existing prototype achieves ~60% structural alignment with the new design goals. The **core run orchestration, executor plugin model, and remote-agent protocol** provide a solid foundation for multi-target, declarative testing. However, **three critical gaps demand rebuilds**: (1) the rigidly hardcoded six-step stage model must become extensible handler-declared stages; (2) multi-target service-principal management is absent (auth defaults to single app SP + demo targets); (3) safety class enforcement for destructive operations is unimplemented. The existing **poller, persistence layer, and analytics** require substantial rework to support ~100 per-target SPs, regression tracking, and CI-first blocking semantics. The AI authoring service is well-positioned and needs only minor extension.

---

## Inventory Table

| Component | What it does today | Verdict | Why |
|-----------|-------------------|---------|-----|
| `backend/app/main.py` | FastAPI app shell + React SPA serving | **KEEP** | This structure directly supports all invocation modes (CI, GUI, scheduled). No redesign needed. |
| `backend/app/core/config.py` | Dual-mode auth (app SP in workspace, local profile for dev); Lakebase detection | **ADAPT** | Auth model assumes single app SP. New design requires per-target SP switching. Add SafetyClass enum + target-specific credential factory. |
| `backend/app/core/enums.py` | Fixed domain vocabulary: ProbeOutcome, ConnectivityOutcome, ExecutionMode (PREFLIGHT/SMOKE/FULL), StepKind (6 hardcoded steps), RootCauseBucket taxonomy | **REBUILD** | "Extensible stages declared by handlers" incompatible with hardcoded StepKind enum. Stages become metadata (not enums). RootCauseBucket remains, but needs safety-class-specific failure modes. Remove execution-mode-to-stage mapping; make stages self-describing. |
| `backend/app/models/models.py` | TestDefinition, TestRun, TestRunStep, TestTarget, RunRequest, Schedule, RemoteAgent, Artifact | **ADAPT** | Core tables work, but need: (1) TestTarget.safety_class (read-only / sandbox / prod-for-us); (2) TestDefinition.stages (JSON list replacing executor_key→fixed steps); (3) TestRun.service_principal_id; (4) TestRunStep removal (→ dynamic step table mirroring declared stages); (5) flakiness/regression tracking fields (consecutive_pass_count, seen_since_git_sha). |
| `backend/app/executors/base.py` | Executor ABC + orchestration logic; implements six-step contract (PRECHECK→AUTH→DNS→NETWORK→FUNCTIONAL→CLEANUP) with short-circuit on failure | **REBUILD** | Hardcoded six-step orchestration is a blocker. New design: executor declares stages in `stages_sequence()`, base class iterates dynamically. Short-circuit logic stays, but become configurable. Cleanup becomes a stage, not a special-case finally block. |
| `backend/app/executors/registry.py` | Metadata-driven executor registration by key | **KEEP** | Plugin model is sound. No change needed. |
| `backend/app/executors/builtin/workspace_probes.py` | databricks_app_health, unity_catalog_query, model_serving_invoke probes | **ADAPT** | Probes stay, but must declare stages explicitly. Update to inherit new Executor base. Test parametrization (warehouse_id, catalog) unchanged. |
| `backend/app/executors/builtin/sql_warehouse.py` | SQL warehouse query probe | **ADAPT** | Similar to workspace_probes: declare stages, update base inheritance. |
| `backend/app/executors/builtin/outbound_http.py` | HTTP outbound connectivity (DNS + TLS + request) | **ADAPT** | Already granular (DNS, TLS, request). Adapt to new stage model. |
| `backend/app/executors/builtin/remote_agent.py` | Control-plane stub for remote agents; produces PARTIAL step awaiting signed results | **ADAPT** | Stub remains, but becomes a stage factory. Remote agents post results conforming to new stage model. Update protocol to support dynamic stages (see shared/remote_agent_protocol.md). |
| `backend/app/executors/builtin/lakebase.py` | Lakebase (Postgres) smoke test | **ADAPT** | Minor: declare stages, update base class. |
| `backend/app/executors/builtin/stubs.py` | Capability-gated stub (graceful skip on unknown executor_key) | **KEEP** | Fallback logic unchanged; works with dynamic stages. |
| `backend/app/services/run_service.py` | Run orchestration: submission, fan-out, execution, aggregation (connectable vs functional verdict) | **REBUILD** | Major rework needed: (1) multi-target SP switching per run; (2) destructive-op gating via safety_class; (3) CI pass/fail verdict instead of async-only submission; (4) regression detection (compare against baseline/main branch); (5) flakiness tracking; (6) dynamic stage aggregation instead of hardcoded six-step rollup. |
| `backend/app/workers/poller.py` | Async background processor; polls PENDING run_requests, expands, executes, writes results | **ADAPT** | Poller becomes a worker pool coordinator across ~100 targets. Needs: (1) per-target worker assignment (affinity); (2) SP token refresh per worker; (3) request claim locking across workers; (4) support for CI synchronous return (inline execution). |
| `backend/app/api/v1/runs.py` | Run submission (async + idempotent), history, detail endpoints | **ADAPT** | Needs: (1) `inline=true` → synchronous execution + blocking on pass/fail for CI; (2) response includes run steps + dynamic stage names (not fixed StepKind); (3) baseline/comparison context for regression detection. |
| `backend/app/api/v1/targets.py` | Target CRUD (workspace, catalog, external_system descriptors) | **ADAPT** | Add safety_class (read-only / sandbox / prod-for-us) enum field; enforce in run engine. Add service_principal_id or binding hint. |
| `backend/app/api/v1/schedules.py` | Schedule CRUD + cron→Databricks Job materialization | **ADAPT** | Extend to fan-out across ~100 targets. Each fire becomes: for each target → create run_request(target_id). Bundle job invocation logic stays. |
| `backend/app/api/v1/agents.py` | Remote agent registration, heartbeat, signed result ingestion | **KEEP** | Protocol already supports multiple agents across vantage points. Extend for dynamic stages in result payload. |
| `backend/app/api/v1/authoring.py` | AI test generation from NL description | **KEEP** | Already uses Model-Serving endpoint + constrained to registered executors. Minor: update executor guide to reflect new stage model. |
| `backend/app/api/v1/audit.py` | Audit trail recording (actor, action, before/after state) | **KEEP** | Core audit unchanged; audits destructive-op gating + SP swaps. |
| `backend/app/api/v1/analytics.py` | Failure analytics: overview, failure_rate_over_time, top_failing, regression_detection | **REBUILD** | Current: simple failure counts. New: (1) regression delta (failures since main branch); (2) flakiness detection (pass/fail churn per test per target); (3) safety-class-aware (prod failures surface higher priority); (4) per-target SLA tracking. Queries scale to ~100 targets. |
| `backend/app/api/v1/admin.py` | Admin endpoints (seed, status, job trigger) | **ADAPT** | Extend to support per-target seeding, multi-SP validation, safety class enforcement checks. |
| `backend/app/services/authoring.py` | NL → TestDefinitionCreate spec generation via Model Serving | **ADAPT** | Update EXECUTOR_GUIDE to reflect new stage metadata. Spec output unchanged; validation in POST /tests stays the same. |
| `backend/app/services/schedule_service.py` | Schedule → Databricks Job materialization (cron + params) | **ADAPT** | Extend parameter injection to include target_id or loop over targets. Per-target scheduling logic. |
| `backend/app/services/audit.py` | Record audit events to durable table | **KEEP** | Unchanged. |
| `backend/app/services/analytics.py` | Compute overview, failure trends, top-failing tests | **REBUILD** | Extend with regression detection (baseline comparison), flakiness scoring, per-target SLA metrics. |
| `backend/app/services/seed.py` | Seed test definitions, suites, targets, synthetic historical runs | **ADAPT** | Update CATALOG to include stage declarations + new executor parameters. Add per-target seed support. |
| `backend/app/clients/databricks.py` | Thin SDK wrappers: job trigger, cron job upsert, workspace context | **ADAPT** | Add per-target workspace client factory. Add SP token refresh logic. Workspace context → per-target context. |
| `backend/app/clients/model_serving.py` | Foundation Model chat endpoint client | **KEEP** | Unchanged; used only for authoring. |
| `backend/app/db/session.py` | SQLAlchemy + Lakebase + SQLite session management | **KEEP** | Core DB layer unchanged. May add per-target connection pool. |
| `backend/app/schemas/schemas.py` | Pydantic request/response schemas | **ADAPT** | Add SafetyClass, dynamic stage fields, multi-target parameters. |
| `backend/app/telemetry/otel.py` | OpenTelemetry instrumentation | **KEEP** | Add per-target/SP tracing context. |
| `backend/app/core/ids.py` | ID generation (snowflake-style, utcnow) | **KEEP** | Unchanged. |
| `backend/app/core/logging.py` | Structured logging setup | **KEEP** | Unchanged. |
| `databricks.yml` | Databricks Asset Bundle: Lakebase, App, scheduled Job | **KEEP** | DAB + app shell are being kept as-is per spec. No redesign. |
| `app.yaml` | Direct app deploy config (fallback to bundle) | **KEEP** | No changes needed. |
| `databricks/jobs/run_suite.py` | Scheduled job entrypoint: fire schedule or process request | **ADAPT** | Extend to loop over targets; invoke run_service.process_request(request_id, target_id, sp_id). |
| `infra/sql/*` | Unity Catalog / Databricks SQL views for analytics | **ADAPT** | Extend views to support regression deltas, flakiness metrics, per-target SLA views. |
| `src/` (React frontend) | UI: test definitions, suites, run history, charts | **REBUILD** (partial) | Add regression view (what changed since main), root-cause triage (prioritize failures), flakiness display, per-target SLA dashboard. Run detail: show dynamic stage names (not hardcoded StepKind). |
| `tests/` | Unit + integration test suite (registry, executors, run lifecycle) | **ADAPT** | Add tests for multi-target SP switching, safety-class gating, dynamic stage execution, regression tracking. |
| `shared/remote_agent_protocol.md` | Remote agent registration + signed result handshake spec | **ADAPT** | Extend result payload to declare stages (not hardcoded). Update signature scope. |
| `shared/enums.json` | Enum values exported for UI/agents (RootCauseBucket, etc.) | **REBUILD** | Replace StepKind with dynamic stage names. Keep RootCauseBucket. Add SafetyClass. |

---

## Key Tensions / Risks

### 1. **Hardcoded Six-Step Stage Model vs. Extensible Handler-Declared Stages**
- **Current state**: Every executor runs PRECHECK→AUTH→DNS→NETWORK→FUNCTIONAL→CLEANUP. StepKind is a hardcoded enum. Orchestration in `base.py` maps ExecutionMode to a fixed step sequence.
- **New design**: Each executor declares its stages dynamically (e.g., "resolve → TCP → TLS → OAuth → auth → operation → correctness"). Stages are metadata (JSON), not enums.
- **Risk**: This is the deepest refactor—touches every executor, aggregation logic, step storage, UI display, and remote-agent protocol. Must avoid a lengthy transition; recommend a parallel executor API so old + new can coexist during migration.
- **Recommended approach**: Add `stages_sequence()` method to Executor; default impl maps ExecutionMode to legacy six steps for backward compat. New executors override. Modify TestRunStep table to store `stage_name: str` (not enum). Aggregation logic becomes stage-agnostic (look for first failure, not specific StepKind values).

### 2. **Single App Service Principal vs. Per-Target SP Management**
- **Current state**: Auth defaults to the app's injected service principal (Databricks Apps) or local profile (dev). Targets are purely metadata (workspace_id, catalog, config). No credential binding per target.
- **New design**: ~100 targets, each with its own SP. CI must specify target + SP identity. Tests run under target-specific SP.
- **Risk**: Major auth refactor. Credential storage (Databricks secret scopes?), token refresh, multi-client pooling, request-time SP selection. If SPs aren't pre-shared with the app, access patterns break.
- **Recommended approach**: (1) Store SP client_id (not secret!) in TestTarget.config as hint. (2) Require deployer to pre-grant each target SP read/query access to Lakebase so the app can write results on their behalf. (3) At run time, if target.sp_hint is set, use it; else fall back to app SP. (4) Add per-target workspace client factory + connection pool in `backend/app/clients/databricks.py`.

### 3. **CI Pass/Fail Verdict Blocking vs. Async Polling**
- **Current state**: Submission is async + idempotent (returns immediately with request_id in PENDING state). Poller processes in background. No synchronous pass/fail for CI.
- **New design**: GitHub Action calls API, awaits blocking pass/fail verdict, gates next pipeline step.
- **Risk**: API must support synchronous execution option without starving the async queue. Timeout handling critical (how long does CI wait?).
- **Recommended approach**: Extend `POST /runs` to accept `?sync=true + ?timeout_seconds=120`. When sync=true, inline-execute the request, await all runs, aggregate verdict, return in response. If timeout elapses, return 408 (request timeout) so CI can retry or escalate.

### 4. **Safety Class Enforcement (Read-Only / Sandbox / Prod-for-Us) at Engine Level**
- **Current state**: Tests can destructive=true, but only a flag; no gating at execution time. No safety class concept.
- **New design**: TestTarget.safety_class must hard-gate destructive operations. Destructive ops on prod-for-us or read-only targets must fail immediately.
- **Risk**: Requires schema change + runtime validation. Misconfiguration could enable destructive ops on prod.
- **Recommended approach**: (1) Add TestTarget.safety_class enum (READ_ONLY, SANDBOX, PROD_FOR_US). (2) In run_service.execute_run(), check `if definition.destructive and target.safety_class != SANDBOX: raise ProbeError(...)`. (3) Test extensively. (4) Audit all runs against safety_class for compliance.

### 5. **Regression & Flakiness Detection (Complex Analytics)**
- **Current state**: Analytics computes pass rate, failure rate over time, top-failing tests. No regression delta (vs. main) or flakiness scoring.
- **New design**: "What regressed since a change" + flakiness detection (test passes/fails unpredictably).
- **Risk**: Requires baseline runs on main branch (must be stored separately), per-test per-target flakiness score (complex aggregation), and UI triage logic.
- **Recommended approach**: (1) Store git_sha per run (already done). (2) Add baseline_run_id to TestRun (link to main-branch run for comparison). (3) Regression query: `WHERE target_id=X AND test_id=Y AND baseline_run.status=PASSED AND current_run.status=FAILED`. (4) Flakiness: consecutive_pass_count + variance in outcome (7-day window). (5) Triage view: sort by (safety_class DESC, regression_count DESC, flakiness_score DESC). UI highlights failures that are **new** (regressed) or **unreliable** (flaky).

### 6. **Multi-Target Scheduling: Scale to ~100 Targets**
- **Current state**: Schedule has suite_ids/test_ids/target_ids/catalogs fields. A single fire creates one run_request with all selections. Poller fans out 1-to-many internally.
- **New design**: Each fire must loop: for each target → create run_request(target_id). Schedule logic becomes per-target.
- **Risk**: If a schedule selects "all tests x all targets", a nightly fire creates 100–1000s of run_requests. Poller must not be starved; worker pool sizing critical.
- **Recommended approach**: (1) Extend schedule_service.fire_schedule() to accept target_ids list. (2) If not specified, derive from enabled targets. (3) Loop: for each target, create run_request(target_id). (4) Poller batch size should account for ~100 targets × ~10 tests = ~1000 runs per cycle. Monitor queue depth; consider a separate high-throughput worker pool for scheduled runs. (5) Consider sharding: hash(target_id) % num_workers to improve cache locality.

### 7. **Remote Agent Protocol Extensibility**
- **Current state**: Agent posts results with hardcoded steps (AUTH, DNS, NETWORK, FUNCTIONAL, CLEANUP). Signature scope is over (run_id, agent_id, steps).
- **New design**: Agent must post stages dynamically declared by its executor.
- **Risk**: If stages are no longer an enum, signature verification breaks (agent and control plane must agree on stage order & names for canonical JSON).
- **Recommended approach**: (1) Extend remote_agent_protocol.md to specify stage names at registration time (or in result payload). (2) Signature scope expands: `{run_id, agent_id, stages_sequence: [...], steps: [...]}`. (3) Control plane verifies signature using agent's declared stages (not global enum). Canonical JSON: sort_keys=true, and ensure stage order is deterministic.

### 8. **Poller Coordination & Request Claiming at Scale**
- **Current state**: Poller claims PENDING run_requests with a simple `locked_by + locked_at` mechanism. Single poller can handle ~25 requests per cycle.
- **New design**: With ~100 targets × ~10 tests/night + manual runs, ~1000+ requests/day. Multiple pollers (one per target? or shared pool?) must coordinate.
- **Risk**: Claim contention, locks held too long, deadlocks.
- **Recommended approach**: (1) Upgrade locking from timestamp-based to **distributed lock (Redis/Zookeeper)** OR (2) use database row-level locks with shorter timeouts (5 min, not 15 min). (3) Add poison-pill retry limit (max 3 failures → mark request as ERROR, alert). (4) Poller workers should have affinity to targets (worker-A handles target-set-1, worker-B handles target-set-2) to improve cache / SP token reuse.

---

## Recommended Starting Point for Rebuild

### Phase 1: Extensible Stage Foundation (Critical Path)
1. **Decouple stages from enums**: Modify `TestRunStep` schema to store `stage_name: str` instead of `step_kind: StepKind`. Backfill existing data or migrate separately.
2. **Executor API**: Add `stages_sequence(execution_mode: ExecutionMode) -> list[StageDeclaration]` to Executor base. Legacy default returns six-step sequence. New executors override.
3. **Aggregation**: Rewrite `_aggregate_run()` in run_service.py to iterate over declared stages, not hardcoded StepKind values.
4. **DB schema**: Add `TestTarget.safety_class` enum. Update migrations.
5. **Validation**: Add ProbeError classification for safety-class violations.

### Phase 2: Multi-Target Service Principal Support
1. **Target binding**: Add `TestTarget.service_principal_hint` (client_id, not secret). Schema migration.
2. **Auth factory**: Extend `backend/app/clients/databricks.py` with `get_target_workspace_client(target: TestTarget)` that uses target's SP (if configured).
3. **Per-run SP context**: Pass target's SP through ProbeContext → executor. Executors use it implicitly.
4. **Testing**: Unit tests for SP switching; integration tests against real targets if available.

### Phase 3: CI Blocking + Regression Foundation
1. **Sync execution**: Extend `POST /runs` with `sync=true` + `timeout_seconds`. When sync, inline-execute, return verdict.
2. **Baseline storage**: Extend TestRun to store `baseline_run_id` (link to main-branch run). Seed baseline runs for regression comparison.
3. **Regression query**: Add `regression_detected(test_id, target_id, baseline) -> bool` analytics function.
4. **API response**: Include regression delta in run response: `{"passed": true, "regressed": false, "details": {...}}`.

### Phase 4: Analytics Rebuild (Triage View)
1. **Flakiness score**: Compute 7-day variance in outcome per (test_id, target_id).
2. **Triage aggregation**: `top_failures(limit=20, sort_by=["regression", "flakiness", "prod_failures"])`.
3. **UI triage view**: Show failures sorted by impact (prod > sandbox > read-only), regression, flakiness. Root-cause pie chart per target.
4. **SLA tracking**: For each safety_class, compute pass_rate (prod ≥ 95%? sandbox ≥ 90%?). Alert on SLA breach.

### Phase 5: Scale & Performance (Post-MVP)
1. **Poller sharding**: Worker pool with per-target affinity. Distributed locking.
2. **Schedule fan-out**: For each target, create run_request; batch them by target.
3. **Remote agent extensibility**: Update protocol + signature scheme for dynamic stages.
4. **Frontend rebuild**: Regression/triage views, per-target dashboards, dynamic stage names in run detail.

---

## Files to Review / Modify During Rebuild

### Must refactor immediately (Critical path):
- `backend/app/core/enums.py` — Remove StepKind, keep others
- `backend/app/models/models.py` — TestRunStep, TestTarget, TestRun schema changes
- `backend/app/executors/base.py` — Orchestration logic rewrite
- `backend/app/services/run_service.py` — Aggregation + multi-target SP
- `backend/app/migrations/versions/*.py` — Alembic migrations for schema changes
- `shared/enums.json` — Rebuild stage/taxonomy exports

### High-priority adaptations (Phase 1–3):
- `backend/app/clients/databricks.py` — Multi-SP support
- `backend/app/api/v1/runs.py` — Sync execution + CI verdict
- `backend/app/services/schedule_service.py` — Per-target fan-out
- `backend/app/services/analytics.py` — Regression + flakiness detection
- `backend/app/api/v1/targets.py` — Safety class field

### Lower-priority updates (Phase 4–5):
- All builtin executors — Stage declaration
- Frontend (`src/`) — Triage view, dynamic stages
- `databricks/jobs/run_suite.py` — Per-target loop
- `tests/` — New tests for multi-target, safety class, dynamic stages
- `shared/remote_agent_protocol.md` — Stage extensibility

---

## Conclusion

The existing codebase is a **well-structured prototype** with strong foundations in run orchestration, plugin architecture, and remote-agent design. The shift to extensible stages, multi-target SP management, and regression detection is a **significant but manageable rebuild**. The recommended phased approach decouples these concerns so early wins (phases 1–3) unblock CI integration and multi-target basic operation, while phases 4–5 polish the triage/analytics experience at scale. The DAB + app shell, auth foundation, and authoring service require only minor adaptation and can remain stable during the rebuild.

**Estimated effort**: ~8–10 weeks of focused engineering (4–5 developers, or 2 developers part-time over a longer cycle).
