"""Connectivity: can the platform reach a workspace's SQL, Unity Catalog and the internet.

Three read-only Tests, one claim each, grouped in the **Connectivity Suite**.
They predate the health tiers and need no canary fixtures, so they work against
any registered workspace: a warehouse answers a statement, Unity Catalog lists
catalogs through it, and the platform's own network reaches an external HTTPS
endpoint.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

SQL_WAREHOUSE_VALIDATION = '''"""SQL Warehouse validation

A SQL warehouse runs a statement for the target's identity and returns its
result: the warehouse is reachable, starts if it was stopped, and the identity
may use it.

Target tags (or run params):
  warehouse_id   the SQL warehouse to use (default: a running or serverless one)
"""
with check("a SQL warehouse returns the healthcheck row"):
    rows = sql("SELECT 1 AS healthcheck")
    record("rows", rows)
    assert len(rows) == 1, f"expected 1 row from SELECT 1, got {len(rows)}"
    assert rows[0].healthcheck == "1", f"healthcheck is {rows[0].healthcheck!r}, expected '1'"
'''

UNITY_CATALOG_ACCESS = '''"""Unity Catalog access

The target's identity sees Unity Catalog through a SQL warehouse: SHOW CATALOGS
lists at least one catalog. An empty list means the workspace has no metastore
assigned or the identity holds no grant on any catalog.

Target tags (or run params):
  warehouse_id   the SQL warehouse to use (default: a running or serverless one)
"""
with check("the identity can see at least one catalog"):
    catalogs = [row.catalog for row in sql("SHOW CATALOGS")]
    record("catalogs", catalogs)
    assert catalogs, "SHOW CATALOGS returned no catalogs"
'''

EGRESS_CONNECTIVITY = '''"""Egress connectivity

The platform's network reaches an external HTTPS endpoint, so the outbound path
the platform's own calls take is open. This is the platform's egress, not a
cluster's: a compute probe covers that.

Target tags (or run params):
  egress_probe_url   the URL to request (default https://example.com)
"""
URL = setting("egress_probe_url", "https://example.com")

with check("an external HTTPS endpoint answers without an error status"):
    response = http(URL)
    record("url", URL)
    record("status", response.status_code)
    assert response.status_code < 400, f"{URL} answered HTTP {response.status_code}"
'''


TESTS: dict[str, TestSpec] = {
    "sql_warehouse_validation": TestSpec(
        "SQL Warehouse validation",
        "A SQL warehouse is reachable and returns the result of SELECT 1.",
        tags=["sql", "consumer", "connectivity"],
        script=SQL_WAREHOUSE_VALIDATION,
    ),
    "unity_catalog_access": TestSpec(
        "Unity Catalog access",
        "Unity Catalog is visible through a SQL warehouse: SHOW CATALOGS lists at least one catalog.",
        tags=["uc", "consumer", "governance"],
        script=UNITY_CATALOG_ACCESS,
    ),
    "egress_connectivity": TestSpec(
        "Egress connectivity",
        "The platform reaches an external HTTPS endpoint (egress_probe_url, default https://example.com).",
        tags=["consumer", "egress"],
        script=EGRESS_CONNECTIVITY,
    ),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Connectivity Suite", "Reachability across producer/consumer paths.",
              ["egress_connectivity", "sql_warehouse_validation", "unity_catalog_access"]),
]


CONTRIBUTION = Contribution(
    tests=TESTS,
    suites=SUITES,
)
