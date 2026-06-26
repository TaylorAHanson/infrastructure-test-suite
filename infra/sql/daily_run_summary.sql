-- Daily run summary: volume, pass/fail, connectivity split, and MTTR inputs.
CREATE OR REPLACE VIEW ${ANALYTICS_CATALOG}.${ANALYTICS_SCHEMA}.daily_run_summary AS
SELECT
  CAST(finished_at AS DATE)                                              AS run_date,
  COUNT(*)                                                               AS total_runs,
  SUM(CASE WHEN probe_outcome = 'PASS' THEN 1 ELSE 0 END)               AS passed,
  SUM(CASE WHEN status IN ('FAILED', 'ERROR') THEN 1 ELSE 0 END)        AS failed,
  SUM(CASE WHEN connectivity_outcome = 'NOT_CONNECTABLE' THEN 1 ELSE 0 END) AS connectivity_failures,
  SUM(CASE WHEN status IN ('FAILED','ERROR')
            AND connectivity_outcome <> 'NOT_CONNECTABLE' THEN 1 ELSE 0 END) AS functional_failures,
  ROUND(AVG(duration_ms))                                                AS avg_duration_ms,
  PERCENTILE(duration_ms, 0.5)                                           AS p50_duration_ms,
  PERCENTILE(duration_ms, 0.95)                                          AS p95_duration_ms
FROM ${SRC_CATALOG}.${SRC_SCHEMA}.test_runs
WHERE finished_at IS NOT NULL
GROUP BY CAST(finished_at AS DATE);
