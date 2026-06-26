-- Failure rate per test definition, with last-success / last-failure markers.
CREATE OR REPLACE VIEW ${ANALYTICS_CATALOG}.${ANALYTICS_SCHEMA}.failure_rate_by_test AS
SELECT
  d.id                                                                  AS test_id,
  d.display_name                                                        AS test_name,
  d.category                                                            AS category,
  COUNT(r.run_id)                                                       AS total_runs,
  SUM(CASE WHEN r.status IN ('FAILED','ERROR') THEN 1 ELSE 0 END)       AS failures,
  ROUND(
    SUM(CASE WHEN r.status IN ('FAILED','ERROR') THEN 1 ELSE 0 END) / NULLIF(COUNT(r.run_id), 0), 4
  )                                                                     AS failure_rate,
  MAX(CASE WHEN r.probe_outcome = 'PASS' THEN r.finished_at END)        AS last_success_at,
  MAX(CASE WHEN r.status IN ('FAILED','ERROR') THEN r.finished_at END)  AS last_failure_at
FROM ${SRC_CATALOG}.${SRC_SCHEMA}.test_definitions d
LEFT JOIN ${SRC_CATALOG}.${SRC_SCHEMA}.test_runs r ON r.test_definition_id = d.id
GROUP BY d.id, d.display_name, d.category;
