# Research: what should "Databricks is still working after `terraform apply`" actually test?

*Researched 2026-10-02. Sources: Databricks SDK source (pinned in `backend/venv`), this repo's code, docs.databricks.com / KB, internal Confluence + Glean + Slack (via Glean). Claims marked **[unverified]** came only from Glean AI summaries or couldn't be traced to a primary source — confirm before relying on them.*

## TL;DR

1. **The current catalog mostly measures the wrong thing.** About 25 of 31 seeded Checks are control-plane `list`/`get` calls. They prove the REST API answered an authenticated call, and nothing else. A terraform apply that breaks classic-compute networking, back-end PrivateLink, a storage credential's IAM trust, CMK, or NCC egress leaves almost all of them green. The control plane doesn't run in the customer's VPC, so it keeps answering.
2. **The catalog's checks don't measure what their names say, and several can't run live at all** (verified in code, §4):
   - The four Security Posture assertions compare against fields that only exist in `demo_evidence`, so on a live workspace they **always fail**.
   - `job_smoke` crashes after submitting the job and never waits for the run. The catalog's only functional test **cannot pass live**.
   - Counterfactuals **pass on any failure**, including a total outage.
   - Several checks call **admin-only** APIs, but the per-target SP is never an admin.
3. **What to build instead:** a small set of **golden-path canaries**. Each one exercises one layer terraform manages, against **canary objects that terraform itself provisions** in every workspace. Run them in two tiers (§3):
   - a fast read-only tier for every apply;
   - a "launch real compute" tier for network-touching applies and nightly.
   That second tier is the only way to catch the most common post-change failures: cluster bootstrap, Secure Cluster Connectivity relay, subnet exhaustion, and VPC endpoints.
4. **Split posture from health.** SSO, PAT hygiene, IP ACLs, admin counts and No-Isolation clusters are compliance drift, not "is it working". Move them to a separate, non-gating posture suite run by an admin-read identity, or hand them to **SAT**, which already covers them better.

## 1. Framing: what can a `terraform apply` break?

A Databricks deployment has layers. Terraform touches all of them, but the current suite only observes the top one.

| Layer | Typical terraform resources | Observable from... |
|---|---|---|
| Control plane API / auth | `databricks_mws_workspaces`, IP access lists, SP/OAuth | any API call (current suite) |
| Identity & entitlements | SCIM/groups, `databricks_permissions`, entitlements | API calls *as the identity* |
| UC metadata | metastore assignment, catalog bindings, `databricks_grants` | UC API / SQL |
| **UC storage path** | storage credentials (IAM role + trust/external ID), external locations, CMK | **actually reading/writing data** |
| **Classic data plane** | VPC, subnets, SGs/NACLs, routes/NAT, VPC endpoints (S3/STS/Kinesis), back-end PrivateLink, SCC relay | **launching classic compute** |
| **Serverless data plane** | NCC, private endpoint rules, serverless network policies | **running serverless SQL/jobs** |
| Egress | firewall, network policies | probe *from* the compute in question |

Two traps for terraform-managed Databricks specifically:

- **Authoritative resources wipe state silently.** For example, `databricks_permissions` and `databricks_grants` replace the full ACL; they don't add to it. "Can my workload identity still read X" is therefore a first-class regression. (Terraform provider docs; the field reports this often. Frequency **[unverified]**.)
- **Force-new replacement** can destroy and recreate a workspace or metastore assignment from a minor variable change, especially across provider upgrades (terraform-provider-databricks issues; frequency **[unverified]**). The cheapest catch for that is a **pre-apply `terraform plan` check**, not this suite. See §5.

**Propagation windows.** Network-config and PrivateLink changes are documented as taking up to ~20 minutes to apply. Serverless network-policy changes reportedly take ~10 minutes, and running serverless resources must restart to pick them up **[unverified, Glean]**. Recommendation: fail-closed only after a retry/settle window, not at T+0.

**Vantage-point caveat.** The app runs as a Databricks App, which is serverless and Databricks-hosted. Its probes test the **Databricks-serverless → workspace** path. They do **not** test corporate network → workspace (front-end PrivateLink, user DNS), and they don't test the classic data plane unless the probe launches classic compute and runs *inside* it. That is the main argument for Tier B below.

## 2. What the field / Databricks already do (prior art)

- **SAT (Security Analysis Tool)** — https://github.com/databricks-industry-solutions/security-analysis-tool. It runs dozens of **posture** checks: network, identity, data protection, governance. It runs with admin-level credentials. It is the obvious home for this repo's Security Posture / Identity drift checks, rather than re-implementing them.
- **Security Reference Architecture (SRA)** — https://github.com/databricks/terraform-databricks-sra. A secure-by-default terraform template set. It's posture by construction, not a post-apply health suite.
- **PrivateLink/network validation** (Private Link FAQ, Confluence UN space; docs `privatelink-dns`). Standard procedure is run *from compute*:
  - `nslookup` the workspace FQDN and expect a private IP;
  - `nc -zv <fqdn> 443`;
  - for the classic SCC relay, `nc -zv tunnel.privatelink.<region>.cloud.databricks.com 6666`;
  - `openssl s_client` for TLS;
  - curl to S3/STS endpoints.
  This is exactly the probe set Tier B should run inside a job.
- **KB: cluster bootstrap / subnet exhaustion** — `BOOTSTRAP_TIMEOUT` and `AWS_INSUFFICIENT_FREE_ADDRESSES_IN_SUBNET_FAILURE`. These surface **only at compute launch** (kb.databricks.com/en_US/clusters/cluster-fails-to-launch-with-a-bootstrap-timeout-error; …/clusters-terminating-with-error-aws_insufficient_free_addresses_in_subnet_failure-client_error).
- **KB: storage credential external-ID failures** (kb.databricks.com/en_US/unity-catalog/aws-s3-storage-credential-validation-failure-due-to-external-id). The apply succeeds, and the first real UC read fails.
- **DB Sentinel (internal e2e probes)** — Confluence UN 3600057753. Databricks' own pattern is synthetic **end-to-end user journeys**: authenticate as a real test identity, do the real thing, verify. That is not a fleet of list calls, and it's the model to copy.
- **Workspace-creation docs** only validate "API answers + UI loads". That is roughly what the current suite does, and the docs treat it as the floor, not the test.

## 3. Recommended suite

Design principles:

- **Every test maps to a layer from §1.**
- **Assert on specific canary objects, never on "list returned without error".** A list call proves only that the API answered.
- **Canary fixtures are provisioned by the same terraform** (a `validation_canary` module per workspace). A canary failure then means "this apply broke something it manages", and no test depends on whatever ad-hoc state a workspace happens to have.

**Canary fixtures (per workspace, via terraform):**
- catalog `canary`, schema `canary.health`;
- table `canary.health.ping` (1 known row) in managed storage;
- volume `canary.health.files`, backed by the workspace's real external location and storage credential, with one known file;
- schema `canary.scratch` for write round-trips;
- table `canary.health.forbidden`, deliberately **not** granted to the test SP (the counterfactual);
- a small serverless SQL warehouse, plus a classic/pro one if the customer uses classic;
- a test cluster policy;
- the test SP's grants on all of the above.

### Tier A — `quick`, R0, CI gate on every apply (~1–2 min)

| # | Test | How | Catches |
|---|---|---|---|
| A1 | Authenticated API path | `current_user.me` (keep `whoami`) | DNS/TLS/auth from the app's vantage point, IP ACL locking out automation, SP/OAuth breakage |
| A2 | Metastore assignment is *the expected one* | `metastores.current` → assert `metastore_id == <param>` | missing/reassigned metastore, force-new churn |
| A3 | Canary catalog visible & bound | `catalogs.get("canary")` succeeds (the SP has `USE CATALOG`) | catalog-workspace binding loss, grant wipe |
| A4 | Serverless SQL executes | `SELECT 1` on the canary serverless warehouse (keep `sql_healthcheck`) | serverless SQL availability |
| A5 | UC managed-storage read | `SELECT count(*) FROM canary.health.ping` = 1 | UC metadata + managed storage + CMK on the serverless path |
| A6 | External location read | `LIST '/Volumes/canary/health/files'` or `read_files(...)` returns the known file | storage credential IAM trust / external ID, bucket policy, KMS key policy |
| A7 | Grant enforcement (counterfactual) | `SELECT` on `canary.health.forbidden` → **must fail with PERMISSION_DENIED specifically** | over-broad grants from authoritative `databricks_grants`. Needs the §4 evaluator fix |
| A8 | Workload-identity access (optional, per customer) | the same A5 read under each registered Run-as-role | `databricks_permissions`/grants wiping a real team's access |

### Tier B — `full`, R1 (create + cleanup), after network-touching applies + nightly (~5–15 min)

| # | Test | How | Catches |
|---|---|---|---|
| B1 | **Classic compute launch + in-cluster probe** | `jobs.submit` → single-node classic job cluster under the test policy, smallest node. The Python task does all of the following: DNS-resolve the workspace + S3/STS endpoints (assert private IPs where PrivateLink is used), `nc` the SCC relay port, read `canary.health.ping`, read the volume file, write and drop a row in `canary.scratch`. **Wait for terminal state and capture `termination_reason.code` as evidence.** | the #1 real-world post-change class: `BOOTSTRAP_TIMEOUT`, SCC relay / back-end PrivateLink, subnet IP exhaustion, SG/NACL/route/NAT, missing S3/STS/Kinesis VPC endpoints, instance profile, classic-plane UC access |
| B2 | Serverless job | the same task on serverless jobs compute | NCC attachment, private endpoint rules, serverless egress policy |
| B3 | Classic/pro warehouse cold start | start the stopped canary classic warehouse → `SELECT 1` → let it auto-stop | classic SQL in the customer VPC (skip if the customer is serverless-only) |
| B4 | UC write round-trip | in `canary.scratch`: CREATE TABLE → INSERT → SELECT → DROP | write path, managed-location permissions, CMK encrypt (not just decrypt) |
| B5 | Egress allowed / denied pair | from B1/B2 tasks: an allowlisted domain succeeds; a non-allowlisted domain is **denied** (counterfactual) | firewall/network policy too tight *or* too loose |
| B6 | Product surfaces actually used (opt-in per customer) | e.g. query a canary serving endpoint / FM API; open a Lakebase connection; hit a canary App | only if the customer depends on them; otherwise skip |

Map `termination_reason.code` straight into the existing failure taxonomy. The `RootCauseBucket` list in `docs/privatelink-ncc-troubleshooting.md` already anticipates this, so a failed B1 says *why*, not just "failed".

### Tier C — posture/drift suite (separate, never the health gate)

Keep it, but as its own suite, run by an identity that can actually read these settings (admin-read), or replace it with SAT:

- SSO / workspace-conf exfiltration toggles;
- PAT lifetime;
- IP access lists;
- admin count;
- No-Isolation clusters;
- SCIM groups;
- cluster-policy *content* (diff the policy definition against the expected hash, not just "policies list").

## 4. Verdict on the existing catalog

### Bugs found in the code (verified)

1. **Posture assertions can't pass live.** Live evidence is only `{operation, response, row_count}` (`backend/app/handlers/builtin/generic.py:179`, `_response_evidence`). `sso_enabled == 1`, `no_isolation_clusters == 0`, `exfiltration_controls_enabled == 0` and `non_expiring_pats == 0` look up fields that exist only in `demo_evidence`. A missing field evaluates to False (`backend/app/services/assertion_compiler.py:196-198`), so these four CI-gate Tests always fail against a real workspace. Also, `workspace_conf.get_status` returns string values like `"true"`, not the integer `1`.
2. **Counterfactuals pass on any failure.** `_aggregate` maps a DENY expectation to `not operation_succeeded` (`backend/app/services/run_service.py:504-508`, predicate `denied` in `assertion_compiler.py:191-192`). `expected_stage` and the failure bucket are never checked. A DNS outage, a 404, or an expired secret therefore makes every counterfactual **pass**, which contradicts CONTEXT.md ("blocked *at the expected stage*"). Fix: a DENY passes only if the bucket is `PERMISSION_DENIED`, or matches `expected_stage`.
3. **`job_smoke` can't pass live.** `jobs.submit` returns an SDK `Wait`. `Wait.__getattr__` raises `KeyError` (not `AttributeError`), so `hasattr(response, "as_dict")` in `_response_evidence` raises after the job has already been submitted (reproduced). The handler also never calls `.result()`, so "the job run completes" is never observed, and cleanup doesn't run. The `/Shared/job-smoke` notebook it references isn't provisioned anywhere.
4. **The `governance` Test can never be all-green live.** `gov_grant_read_allowed` and `gov_grant_read_denied` make the identical call (`grants.get(catalog, prod)`) with the identical identity. One expects success, the other denial.
5. **Assertions that claim more than they check.** `gov_catalog_isolated` asserts "reports ISOLATED", but it compiles to a bare-success predicate, so isolation mode is never checked. The `cluster_policy_*` checks claim to verify auto-termination and instance/runtime pinning, but they only list policies.

### Per-Check verdicts

Feasibility is for a **non-admin** SP. "Admin-only" is confirmed from SDK docstrings where noted, otherwise **[unverified]**.

| Check | Verdict | Why |
|---|---|---|
| `whoami` | **KEEP** → A1 | the one meaningful control-plane health read |
| `gov_metastore_assigned` | **REWRITE** → A2 | assert the *expected* `metastore_id`, not just success |
| `gov_catalog_isolated` | **REWRITE / MOVE** | hard-codes `prod`; never checks isolation (bug 5). Health version = A3 on `canary`; the isolation-mode assertion moves to posture |
| `gov_external_locations`, `gov_storage_credentials` | **REPLACE** with A6 | list output is visibility-filtered (empty ≠ healthy) and never touches storage. `storage_credentials.validate` would be better but needs owner / `CREATE_EXTERNAL_LOCATION` (SDK docstring), so a real volume read is the least-privilege probe |
| `gov_grant_read_allowed` / `_denied` | **REPLACE** with A5 + A7 | bug 4; use a canary table pair |
| `sql_healthcheck` | **KEEP** → A4 | real execution on a warehouse |
| `uc_listing` (`SHOW CATALOGS`) | **REWRITE** → A3/A5 | listing ≠ access; assert the canary specifically |
| `egress_example` | **REWRITE** → B5 | from the App it tests *the App's* egress, not workloads'. Run it inside B1/B2 and add the denied half |
| `job_smoke` | **REWRITE** → B1 | right idea, broken execution (bug 3); make it the in-cluster probe |
| `cluster_create_allowed` / `_denied` | **REWRITE** | good counterfactual shape, but in a real workspace `clusters.create` launches a cluster. Fold the allowed half into B1 (the job cluster *is* the compliant create) and keep the denied half (cheap: policy rejection happens before launch). Needs the bug-2 fix |
| `lakebase_txn` | **KEEP** (platform self-check) | tests this app, not the target; label it so |
| `serverless_sql_enabled`, `warehouses_list` | **DROP** (duplicates) | same `warehouses.list` twice; A4 subsumes both |
| `jobs_list`, `pipelines_list`, `dlt_pipeline_target_declared` | **DROP** | control-plane reachability only; `pipelines.list_pipelines` appears twice. Re-add a DLT/pipeline run to Tier B only if the customer depends on pipelines |
| `cluster_policy_auto_termination`, `cluster_policy_instance_runtime` | **DROP / MOVE** | duplicates of the same list call; real version is a posture "policy definition matches expected" check |
| `model_serving_enumerates`, `secret_scopes_exist` | **DROP** (or B6) | reachability only; secret *read* of a canary key is the meaningful version if secrets matter |
| `warehouse_healthy_ish` | **KEEP** as tutorial only | same call as `sql_healthcheck`; exists to demo agentic routing |
| `sso_enabled`, `exfiltration_controls_off` | **MOVE** → Tier C | posture; `workspace_conf` read is likely admin-only **[unverified]**; `ssoEnabled` is likely a legacy key on account-level-SSO workspaces **[unverified]**; also bug 1 |
| `pat_expiry_bounded` | **MOVE** → Tier C | posture; `TokenManagementAPI` is admin-only (SDK docstring: "Enables administrators…"); bug 1 |
| `ip_access_list_active` | **MOVE** → Tier C | posture; `IpAccessListsAPI` is admin-only (SDK docstring). Note A1 already catches the dangerous IP-ACL failure (locking out automation) |
| `no_isolation_clusters`, `admin_count_sane`, `scim_groups_present`, `sp_least_privilege` | **MOVE** → Tier C | posture/drift. A non-admin SP sees only its visible clusters/users, so counts are unreliable. `groups.list` is **not** documented admin-only (the audit agent's claim was wrong) |

**Net:** about 31 seeded Checks collapse to roughly 8 Tier A + 5 Tier B health Checks, plus a separate posture suite. Tier A is 8 if A8 is enabled, otherwise 7.

## 5. Out of scope here but high-value

- **Pre-apply plan guard.** Parse `terraform plan -json` and fail CI when it would destroy/replace `databricks_mws_workspaces`, `databricks_metastore_assignment`, `databricks_mws_networks`, storage configs or credentials. It runs before damage is done, and no post-apply test can do that.
- **Front-end / corporate path.** DNS and 443 from the corporate network or BI hosts can't be seen from inside Databricks. That needs an external probe; the retired remote-agent subsystem was the old answer.
- **Cost of Tier B** across ~100 workspaces: single-node smallest instance, nightly + on network-touching applies only. The caller already owns the "what changed → which targets" decision (ADR-0005).

## Evidence quality

- **Strong (verified here):** everything in §4 "Bugs" (repo code + reproduction), and the SDK-docstring admin-only claims (`TokenManagementAPI`, `IpAccessListsAPI`, `StorageCredentialsAPI.validate`).
- **Moderate:** KB articles and public docs cited in §2.
- **Weak / [unverified]:** frequency rankings of failure modes (the ES Jira search failed on auth; ranking came from Glean summaries + Slack volume); the exact propagation windows; `ssoEnabled` legacy status; `workspace_conf` admin requirement; UC binding "eventual consistency" delays.
- **Not found:** a single internal, standardized "post-terraform workspace acceptance test". Field runbooks exist but vary by team and are partly stale (Go-Live checklist, Confluence FE 3025895952, last updated 2023).
