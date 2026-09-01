"""Tutorial/worked-example library (ADR-0006, demoted by Spec #91).

Covers the authoring shapes the model must express: a deterministic
connectivity Check, a functional (row-count) Check, an open-SDK mutating
Check with a **counterfactual** pair, and an **agentic** (non-compilable →
advisory) Check. Every Check and Test here carries :data:`TUTORIAL_TAGS` so
it's distinguishable from the real posture catalog — each domain's own
module under :mod:`app.services.seed.domains` (#107) — that Spec #91 adds
alongside it, without disturbing these exemplars.

The "Producer ingestion access" / "Processing layer access" Tests below are
also referenced by the producer/processing domains' lane Suites
(Producer/Processing Suite) — a cross-module Suite membership the registry
resolves once every domain's Checks/Tests are merged.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

# Applied (via ``hints["tags"]`` on Checks, ``tags`` on Tests) to every exemplar
# below so a fresh boot's catalog is clearly labelled as tutorial content, not
# the operator's real validation battery (Spec #91).
TUTORIAL_TAGS: list[str] = ["tutorial", "example"]


# (key, CheckCreate kwargs). ``key`` is a local handle used to wire Tests below.
# Tutorial/worked-example Checks only — see the catalog domain module for the
# real posture catalog's home.
CHECK_SPECS: list[tuple[str, dict]] = [
    ("sql_healthcheck", dict(
        display_name="SQL warehouse healthcheck",
        description="Run a trivial statement on a SQL warehouse and confirm it returns a row.",
        # A read-only SELECT: declare verb=GET so the coarse heuristic doesn't
        # read "execute_statement" as a mutation (ADR-0017 — the author resolves
        # the verb the heuristic can't infer from an SDK method name).
        operation=dict(service="statement_execution", method="execute_statement", verb="GET",
                       params={"statement": "SELECT 1 AS healthcheck", "demo_row_count": 1}),
        handler_key="sql_warehouse",
        assertion="the query returns at least one row")),
    ("uc_listing", dict(
        display_name="Unity Catalog listing",
        description="List catalogs via the SQL warehouse to prove UC access.",
        operation=dict(service="catalogs", method="list",
                       params={"statement": "SHOW CATALOGS", "demo_row_count": 3}),
        handler_key="sql_warehouse",
        assertion="the query returns at least one row")),
    ("egress_example", dict(
        display_name="Outbound egress to example.com",
        description="Prove the app egress path can reach an external HTTPS endpoint.",
        operation=dict(service="http", method="get",
                       params={"url": "https://example.com", "demo_status_code": 200}),
        handler_key="outbound_http",
        assertion="HTTP status is 200")),
    ("cluster_create_allowed", dict(
        display_name="Cluster create is allowed",
        description="A compliant cluster create succeeds under the workspace policy.",
        operation=dict(service="clusters", method="create", params={}),
        handler_key="databricks_sdk",
        assertion="the operation succeeds")),
    ("cluster_create_denied", dict(
        display_name="Non-compliant cluster create is denied",
        description="A non-compliant cluster create must be denied (fails closed).",
        operation=dict(service="clusters", method="create", params={"demo_outcome": "deny"}),
        handler_key="databricks_sdk",
        assertion="a non-compliant cluster is denied by policy")),
    ("lakebase_txn", dict(
        display_name="Lakebase transaction",
        description="Validate the app's own operational store with a minimal transaction.",
        operation=dict(service="lakebase", method="query", params={}),
        handler_key="lakebase",
        assertion="the transaction succeeds")),
    ("warehouse_healthy_ish", dict(
        display_name="Warehouse came up healthy-ish",
        description="A subjective health read that can only run advisory (agentic).",
        operation=dict(service="statement_execution", method="execute_statement", verb="GET",
                       params={"statement": "SELECT 1", "demo_row_count": 1}),
        handler_key="sql_warehouse",
        assertion="the warehouse came up healthy-ish")),
    # --- Producer-category checks -------------------------------------------
    ("jobs_list", dict(
        display_name="Databricks Jobs list",
        description="List Databricks Jobs to confirm the batch ingestion / workflow layer is reachable.",
        operation=dict(service="jobs", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the job list without error")),
    ("pipelines_list", dict(
        display_name="Delta Live Tables pipeline list",
        description="List DLT pipelines to confirm the streaming/ETL ingestion layer is accessible.",
        operation=dict(service="pipelines", method="list_pipelines", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the pipeline list without error")),
    # --- Processing-category checks -----------------------------------------
    ("warehouses_list", dict(
        display_name="SQL warehouse inventory",
        description="List all SQL warehouses to confirm the compute processing tier can be enumerated.",
        operation=dict(service="warehouses", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        # Reachability assertion only — a fresh workspace may have zero warehouses,
        # so asserting a non-empty result would false-fail a clean install.
        assertion="the call returns the warehouse list without error")),
]


# Tests: target-agnostic bundles, one carrying a counterfactual pair. Every
# entry carries TUTORIAL_TAGS on top of its own tags.
TESTS: dict[str, TestSpec] = {
    "sql": TestSpec("SQL Warehouse validation", "Warehouse reachable + returns rows.",
                    [("sql_healthcheck", "assertion")],
                    ["sql", "consumer", "connectivity", *TUTORIAL_TAGS]),
    "uc": TestSpec("Unity Catalog access", "UC access via the SQL warehouse.",
                   [("uc_listing", "assertion")],
                   ["uc", "consumer", "governance", *TUTORIAL_TAGS]),
    "egress": TestSpec("Egress connectivity", "Outbound HTTPS egress works.",
                       [("egress_example", "assertion")],
                       ["consumer", "egress", *TUTORIAL_TAGS]),
    "policy": TestSpec("Cluster policy enforcement", "Compliant allowed; non-compliant denied.",
                       [("cluster_create_allowed", "assertion"),
                        ("cluster_create_denied", "counterfactual")],
                       ["processing", "policy", "safety", *TUTORIAL_TAGS]),
    "self": TestSpec("Platform self-check", "The app's own store + a health read.",
                     [("lakebase_txn", "assertion"),
                      ("warehouse_healthy_ish", "assertion")],
                     ["platform", *TUTORIAL_TAGS]),
    # --- Producer-category tests ----------------------------------------
    "producer": TestSpec(
        "Producer ingestion access",
        "Databricks Jobs and DLT pipelines are reachable — the ingestion layer is up.",
        [("jobs_list", "assertion"), ("pipelines_list", "assertion")],
        ["producer", "ingestion", "connectivity", *TUTORIAL_TAGS],
    ),
    # --- Processing-category tests --------------------------------------
    "processing": TestSpec(
        "Processing layer access",
        "SQL warehouses can be enumerated — the compute processing tier is reachable.",
        [("warehouses_list", "assertion")],
        ["processing", "compute", "connectivity", *TUTORIAL_TAGS],
    ),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Connectivity Suite", "Reachability across producer/consumer paths.",
              ["egress", "sql", "uc"]),
]


CONTRIBUTION = Contribution(
    check_specs=CHECK_SPECS,
    check_tags=TUTORIAL_TAGS,
    tests=TESTS,
    suites=SUITES,
)
