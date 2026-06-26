-- Failure rate per target (and catalog), to spot environment-specific breakage.
CREATE OR REPLACE VIEW ${ANALYTICS_CATALOG}.${ANALYTICS_SCHEMA}.failure_rate_by_target AS
SELECT
  COALESCE(t.id, '(none)')                                              AS target_id,
  COALESCE(t.name, '(no target)')                                       AS target_name,
  r.catalog                                                             AS catalog,
  COUNT(r.run_id)                                                       AS total_runs,
  SUM(CASE WHEN r.status IN ('FAILED','ERROR') THEN 1 ELSE 0 END)       AS failures,
  SUM(CASE WHEN r.connectivity_outcome = 'NOT_CONNECTABLE' THEN 1 ELSE 0 END) AS connectivity_failures,
  ROUND(
    SUM(CASE WHEN r.status IN ('FAILED','ERROR') THEN 1 ELSE 0 END) / NULLIF(COUNT(r.run_id), 0), 4
  )                                                                     AS failure_rate
FROM ${SRC_CATALOG}.${SRC_SCHEMA}.test_runs r
LEFT JOIN ${SRC_CATALOG}.${SRC_SCHEMA}.test_targets t ON t.id = r.target_id
GROUP BY COALESCE(t.id, '(none)'), COALESCE(t.name, '(no target)'), r.catalog;
