-- Flaky tests: those that both passed AND failed in the trailing 30 days.
CREATE OR REPLACE VIEW ${ANALYTICS_CATALOG}.${ANALYTICS_SCHEMA}.flaky_tests AS
WITH recent AS (
  SELECT *
  FROM ${SRC_CATALOG}.${SRC_SCHEMA}.test_runs
  WHERE finished_at >= DATEADD(day, -30, CURRENT_TIMESTAMP())
)
SELECT
  d.id                                                                  AS test_id,
  d.display_name                                                        AS test_name,
  SUM(CASE WHEN r.probe_outcome = 'PASS' THEN 1 ELSE 0 END)             AS passes,
  SUM(CASE WHEN r.status IN ('FAILED','ERROR') THEN 1 ELSE 0 END)       AS fails,
  ROUND(
    LEAST(
      SUM(CASE WHEN r.probe_outcome = 'PASS' THEN 1 ELSE 0 END),
      SUM(CASE WHEN r.status IN ('FAILED','ERROR') THEN 1 ELSE 0 END)
    ) / NULLIF(COUNT(r.run_id), 0), 4
  )                                                                     AS flip_rate
FROM ${SRC_CATALOG}.${SRC_SCHEMA}.test_definitions d
JOIN recent r ON r.test_definition_id = d.id
GROUP BY d.id, d.display_name
HAVING passes > 0 AND fails > 0
ORDER BY flip_rate DESC;
