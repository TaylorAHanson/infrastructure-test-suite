# Canary fixtures

The seeded health tests don't ask "did the API answer?". They read, write, or get refused by a few **known objects** that you provision in every workspace. So when one fails after a `terraform apply`, the apply broke something it manages.

## What gets created

| Object | Default name | Used by |
|---|---|---|
| Catalog | `validation_canary` (or an existing catalog) | Workspace access: the canary catalog is visible |
| Schema | `health` | everything below |
| Managed table with one row `(1, 'canary')` | `health.ping` | Unity Catalog data path; the compute probes |
| Managed table, **never granted** to the test identity | `health.forbidden` | the counterfactual: an ungranted read must be refused |
| External volume on the workspace's real external location, holding `canary.txt` = `canary` | `health.files` | proves the storage credential's IAM trust, external ID, bucket and KMS policy |
| Schema the test identity may create and write tables in | `scratch` | the write round-trip; the compute probes |

The test identity gets `USE CATALOG`, `USE SCHEMA` on `health`, `SELECT` on `ping`, `READ VOLUME` on `files`, and `USE SCHEMA`, `CREATE TABLE`, `SELECT` and `MODIFY` on `scratch`. It gets nothing on `forbidden`.

## Option 1: Terraform (recommended)

Add `infra/canary/main.tf` from this repository as a module in the same configuration that manages the workspace:

```hcl
module "validation_canary" {
  source          = "./modules/validation_canary"   # a copy of infra/canary
  test_principal  = databricks_service_principal.validation.application_id
  volume_location = "s3://${aws_s3_bucket.external.id}/validation_canary/files"
  warehouse_id    = databricks_sql_endpoint.serverless.id
  storage_root    = "s3://${aws_s3_bucket.external.id}/validation_canary"  # if the metastore has no root
}

output "validation_target_tags" { value = module.validation_canary.target_tags }
```

The module grants with `databricks_grant`, which adds grants for one principal. It deliberately doesn't use `databricks_grants`, which is authoritative and would wipe every other grant on an existing catalog. That same authoritative behaviour, used elsewhere in your configuration, is one of the things the deny check catches.

Terraform can create tables but can't insert rows, so the module writes the two rows with the Databricks CLI (`databricks api post /api/2.0/sql/statements`). The CLI must be authenticated wherever `apply` runs.

## Option 2: one-off with the SDK

For a workspace that Terraform doesn't manage, or just to try things out:

```bash
python infra/canary/provision_canary.py --profile <cli-profile> \
  --principal <test service principal application id> \
  --volume-location s3://<bucket>/validation_canary/files \
  [--storage-root s3://<bucket>/validation_canary] \
  [--catalog <existing catalog> --health-schema validation_health --scratch-schema validation_scratch]
```

The script is idempotent, so you can re-run it to repair drift. It warns if the test identity can already read `forbidden` through an inherited grant. If you don't have `CREATE CATALOG` on the metastore, pass an existing catalog you own with `--catalog`.

## Tell the app where they are

Both options print a set of **target tags**. Set them on the workspace's Target. The Targets page can't edit tags yet, so use the API. `PATCH` replaces the whole tag set:

```bash
curl -X PATCH "$APP_URL/api/v1/targets/<target-id>" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"tags": {"canary_catalog": "validation_canary", "canary_schema": "health", "scratch_schema": "scratch", "warehouse_id": "<warehouse-id>"}}'
```

Each test reads them through `setting("name", default)`. A run parameter wins, then the target tag, then the default. A test whose tag isn't set skips the part that needs it and says which tag to set.

| Tag | Default | Read by |
|---|---|---|
| `canary_catalog` | `validation_canary` | every health test |
| `canary_schema` | `health` | data path, compute probes |
| `scratch_schema` | `scratch` | write round-trip, compute probes |
| `expected_metastore_id` | *(only presence is checked)* | workspace access |
| `warehouse_id` | a running or serverless warehouse | every SQL check |
| `classic_warehouse_id` | *(test skipped)* | SQL warehouse cold start |
| `max_workspace_admins`, `max_admin_service_principals`, `max_token_lifetime_days` | 10, 2, 90 | security posture |
| `egress_allowed_url` | `https://pypi.org/simple/` | compute probes |
| `egress_blocked_url` | *(deny check skipped)* | compute probes |
| `probe_hosts` | *(none)* | compute probes: extra `host:port` pairs, comma-separated |
| `serverless_only` | *(unset)* | set to `true` to skip classic compute on a serverless-only workspace |
| `node_type_id`, `spark_version`, `cluster_policy_id` | a small node, the newest LTS runtime, no policy | classic compute |
| `compute_timeout_minutes` | 20 classic, 15 serverless | compute probes |

## Which suite runs when

- **CI Gate**: Workspace access and Unity Catalog data path. These are read-only and take about a minute. Run them after every apply, on any target class.
- **Post-apply health: compute & network**: classic compute, serverless compute, SQL warehouse cold start, and the Unity Catalog write round-trip. These are R1 and take 5–15 minutes. Run them at **full** depth on `sandbox` or `prod-for-us` targets after applies that touch networking, and nightly. A quick run skips them.
- **Security posture (drift)**: exfiltration settings, token hygiene, IP access lists, user isolation, admin footprint. These are configuration drift, not health, so they never gate. Most need a workspace-admin identity. For a broader battery, use [SAT](https://github.com/databricks-industry-solutions/security-analysis-tool).

## What these tests can't see

The app runs inside Databricks. It can't tell whether your corporate network or BI hosts can still reach the workspace (front-end PrivateLink, corporate DNS). That needs a probe from outside. The cheapest protection of all comes **before** the apply: a CI check on `terraform plan -json` that fails when the plan would destroy or replace a workspace, a metastore assignment, a network configuration or a storage credential.
