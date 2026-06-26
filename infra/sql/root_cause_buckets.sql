-- Normalized root-cause distribution, flagged as connectivity vs functional.
CREATE OR REPLACE VIEW ${ANALYTICS_CATALOG}.${ANALYTICS_SCHEMA}.root_cause_buckets AS
SELECT
  s.root_cause_bucket                                                   AS bucket,
  COUNT(*)                                                              AS occurrences,
  CASE WHEN s.root_cause_bucket IN (
      'DNS_RESOLUTION_FAILURE','PRIVATE_DNS_MISCONFIGURATION','NCC_NOT_ATTACHED',
      'NETWORK_POLICY_DENY','PRIVATELINK_ENDPOINT_NOT_ESTABLISHED','TLS_HANDSHAKE_FAILURE',
      'UPSTREAM_TIMEOUT','EXTERNAL_AGENT_UNAVAILABLE'
  ) THEN TRUE ELSE FALSE END                                            AS is_connectivity
FROM ${SRC_CATALOG}.${SRC_SCHEMA}.test_run_steps s
WHERE s.root_cause_bucket IS NOT NULL
  AND s.outcome IN ('FAIL','ERROR')
GROUP BY s.root_cause_bucket
ORDER BY occurrences DESC;
