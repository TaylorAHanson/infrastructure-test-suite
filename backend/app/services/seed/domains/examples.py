"""Worked-example + onboarding library — the WorkedExample rows, not Check/Test/Suite.

:data:`WORKED_EXAMPLES` is the repo-seeded worked-example library (ADR-0006
§9): gold Tests the Assistant grounds on so an open-source install has a
working assistant on day one.

:data:`ONBOARDING_EXAMPLES` is the curated **onboarding** worked-example set
(spec #56, user stories 30/32): the starter prompts a first-time, untrained
user sees when they open the Assistant with an empty conversation.
Deliberately read-only, single-sentence intents that make the authoring flow
succeed on the first click — distinct from the default Test/Suite catalog
content. They carry the ``onboarding`` tag so the shelf can surface exactly
these via the existing ``search_examples`` path
(``GET /assistant/examples?q=onboarding``).

Both libraries live in the same WorkedExample table and are seeded together
via this module's Contribution.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution

WORKED_EXAMPLES: list[dict] = [
    dict(
        intent="run a SQL query on the warehouse and confirm it returns rows",
        title="SQL warehouse returns rows",
        operation=dict(service="statement_execution", method="execute_statement", verb="GET",
                       params={"statement": "SELECT 1", "demo_row_count": 1}),
        handler_key="sql_warehouse",
        assertion="the query returns at least one row",
        tags=["sql", "warehouse", "connectivity"]),
    dict(
        intent="list Unity Catalog catalogs to prove UC access",
        title="Unity Catalog listing",
        operation=dict(service="catalogs", method="list", params={"demo_row_count": 3}),
        handler_key="sql_warehouse",
        assertion="the query returns at least one row",
        tags=["uc", "governance", "catalog"]),
    dict(
        intent="check outbound egress to an external HTTPS endpoint returns 200",
        title="Outbound egress probe",
        operation=dict(service="http", method="get",
                       params={"url": "https://example.com", "demo_status_code": 200}),
        handler_key="outbound_http",
        assertion="HTTP status is 200",
        tags=["egress", "connectivity", "network"]),
    dict(
        intent="start a cluster under the compute policy and confirm it is allowed",
        title="Cluster policy enforcement (with counterfactual)",
        operation=dict(service="clusters", method="create", params={}),
        handler_key="databricks_sdk",
        assertion="the operation succeeds",
        counterfactual=dict(
            assertion="a non-compliant cluster is denied by policy",
            operation=dict(service="clusters", method="create", params={"demo_outcome": "deny"})),
        tags=["policy", "safety", "cluster"]),
]


ONBOARDING_EXAMPLES: list[dict] = [
    dict(
        intent="list the workspace clusters and confirm at least one comes back",
        title="List clusters",
        operation=dict(service="clusters", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns at least one cluster",
        tags=["onboarding", "clusters", "connectivity"]),
    dict(
        intent="check which identity the platform is running as",
        title="Who am I?",
        operation=dict(service="current_user", method="me", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the current identity",
        tags=["onboarding", "identity"]),
    dict(
        intent="list the jobs in the workspace to confirm access",
        title="List jobs",
        operation=dict(service="jobs", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the job list",
        tags=["onboarding", "jobs"]),
    dict(
        intent="confirm a SQL warehouse is up and reachable",
        title="Warehouse reachable",
        operation=dict(service="warehouses", method="get", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the warehouse is reachable",
        tags=["onboarding", "warehouse", "connectivity"]),
]


CONTRIBUTION = Contribution(
    worked_examples=[*WORKED_EXAMPLES, *ONBOARDING_EXAMPLES],
)
