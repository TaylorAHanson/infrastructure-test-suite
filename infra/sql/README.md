# Analytical views (Unity Catalog / Databricks SQL)

The operational system of record lives in **Lakebase** (Postgres). For
analytical rollups and dashboard charts we expose **Unity Catalog views** over
that data and query them with **Databricks SQL** (a SQL Warehouse).

Two ways to make the operational tables queryable from UC:

1. **Lakehouse Federation** (recommended): register the Lakebase instance as a
   Postgres connection + foreign catalog, then the `run_requests` /
   `test_runs` / `test_run_steps` tables appear as
   `${FED_CATALOG}.${FED_SCHEMA}.<table>`.
2. **Scheduled sync**: a Lakeflow job copies the operational tables into a UC
   managed schema on a cadence.

Replace these placeholders before applying the views (e.g. with `sed` or in the
Databricks Asset Bundle `jobs` task parameters):

- `${ANALYTICS_CATALOG}` / `${ANALYTICS_SCHEMA}` - where the views are created.
- `${SRC_CATALOG}` / `${SRC_SCHEMA}` - where the operational tables are reachable.
- `${OTEL_CATALOG}` / `${OTEL_SCHEMA}` - the app-telemetry schema holding
  `otel_logs`, `otel_spans`, `otel_metrics` (Databricks Apps telemetry, Public
  Preview).

Apply order: the base views first, then `telemetry_joined_events.sql`.
