"""Processing: compute policy + serverless SQL posture (issue #96), split
from the monolithic real-catalog module (#107).

Same shape as the tutorial domain's ``CHECK_SPECS`` — see
:mod:`app.services.seed.domains.producer` for the pattern this and its
sibling posture modules share. Every entry here is a reachability-only
read: a clean workspace may have zero custom cluster policies or warehouses,
so none of these assert non-emptiness.

"Processing Suite" below names ``processing`` and ``policy`` — Test keys the
tutorial domain module owns — alongside this module's own
``processing_posture``: a cross-module Suite membership the registry
resolves once every domain's Checks/Tests are merged (#106).
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

CHECK_SPECS: list[tuple[str, dict]] = [
    ("cluster_policy_auto_termination", dict(
        display_name="Cluster policies enforce auto-termination",
        description="List cluster policies to confirm the compute layer's policies (which pin "
                    "auto-termination) are reachable. A clean workspace may have zero custom "
                    "policies — reachability only, no false-fail.",
        operation=dict(service="cluster_policies", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the cluster policy list without error")),
    ("cluster_policy_instance_runtime", dict(
        display_name="Cluster policies pin approved instance types and runtime",
        description="List cluster policies to confirm the compute layer's policies (which pin "
                    "approved instance types + Databricks Runtime) are reachable. A clean "
                    "workspace may have zero custom policies — reachability only, no false-fail.",
        operation=dict(service="cluster_policies", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the cluster policy list without error")),
    ("serverless_sql_enabled", dict(
        display_name="Serverless SQL is enabled where expected",
        description="List SQL warehouses to confirm the serverless-capable compute tier is "
                    "reachable. A clean workspace may have zero warehouses — reachability only, "
                    "no false-fail.",
        operation=dict(service="warehouses", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the warehouse list without error")),
]


TESTS: dict[str, TestSpec] = {
    "processing_posture": TestSpec(
        "Processing compute posture",
        "Cluster policies enforce auto-termination + pin instance types/runtime; "
        "serverless SQL is enabled where expected.",
        [("cluster_policy_auto_termination", "assertion"),
         ("cluster_policy_instance_runtime", "assertion"),
         ("serverless_sql_enabled", "assertion")],
        ["processing", "compute", "policy", "posture"],
    ),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Processing Suite", "Compute and policy layer validation.",
             ["processing", "policy", "processing_posture"], tags=["seeded", "processing"]),
]


CONTRIBUTION = Contribution(
    check_specs=CHECK_SPECS,
    tests=TESTS,
    suites=SUITES,
)
