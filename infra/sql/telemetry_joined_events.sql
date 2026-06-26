-- Operator view: join application runs with Databricks Apps telemetry (otel)
-- and, where useful, system tables. This correlates a logical run with the
-- app's emitted spans plus any denied outbound attempts on the data path.
--
-- Requires:
--   * Databricks Apps telemetry enabled (otel_logs/otel_spans/otel_metrics).
--   * system.access.outbound_network and system.access.audit readable.
CREATE OR REPLACE VIEW ${ANALYTICS_CATALOG}.${ANALYTICS_SCHEMA}.telemetry_joined_events AS
WITH runs AS (
  SELECT run_id, test_definition_id, status, connectivity_outcome,
         root_cause_bucket, telemetry_trace_id, started_at, finished_at, workspace_id
  FROM ${SRC_CATALOG}.${SRC_SCHEMA}.test_runs
),
spans AS (
  SELECT trace_id, MIN(start_time) AS span_start, MAX(end_time) AS span_end, COUNT(*) AS span_count
  FROM ${OTEL_CATALOG}.${OTEL_SCHEMA}.otel_spans
  GROUP BY trace_id
),
denied_egress AS (
  SELECT
    DATE(event_time) AS d,
    COUNT(*)         AS denied_outbound_attempts
  FROM system.access.outbound_network
  WHERE access_type = 'DENERROR' OR destination_disposition = 'DENIED'
  GROUP BY DATE(event_time)
)
SELECT
  r.run_id,
  r.test_definition_id,
  r.status,
  r.connectivity_outcome,
  r.root_cause_bucket,
  r.started_at,
  r.finished_at,
  sp.span_count,
  sp.span_start,
  sp.span_end,
  de.denied_outbound_attempts
FROM runs r
LEFT JOIN spans sp ON sp.trace_id = r.telemetry_trace_id
LEFT JOIN denied_egress de ON de.d = DATE(r.finished_at);
