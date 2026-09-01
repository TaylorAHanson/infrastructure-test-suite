"""Governance / Unity Catalog posture (issue #95), split from the monolithic
real-catalog module (#107).

Every entry declares ``verb="GET"`` explicitly (R0) and phrases its assertion
as bare success ("... succeeds"/"... completes without error") or a
field-comparison — both compile deterministically via ``compile_assertion``.
The reachability-style entries (``gov_external_locations``,
``gov_storage_credentials``) never assert non-emptiness, so a clean
workspace with none of either doesn't false-fail. ``gov_grant_read_denied``
is a counterfactual: a read the effective identity lacks must be denied,
proving UC enforcement rather than assuming it.

The single ``governance`` Test volunteers for the CI Gate via
``ci_gate_tests`` — every member here, including the counterfactual, is a
read (verb=GET), so the gate's re-derivation keeps it in.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

CHECK_SPECS: list[tuple[str, dict]] = [
    ("gov_metastore_assigned", dict(
        display_name="Unity Catalog metastore is assigned",
        description="Confirm a Unity Catalog metastore is assigned to the workspace — governance is in force.",
        operation=dict(service="metastores", method="current", verb="GET",
                       params={"demo_evidence": {"metastore_id": "demo-metastore-001"}}),
        handler_key="databricks_sdk",
        assertion="the workspace's metastore-assignment lookup succeeds")),
    ("gov_catalog_isolated", dict(
        display_name="Production catalog is bound ISOLATED",
        description="Confirm the production catalog is bound in ISOLATED mode — no cross-environment access.",
        operation=dict(service="catalogs", method="get", verb="GET",
                       params={"name": "prod",
                               "demo_evidence": {"isolation_mode": "ISOLATED"}}),
        handler_key="databricks_sdk",
        assertion="the production catalog lookup succeeds and reports ISOLATED isolation mode")),
    ("gov_external_locations", dict(
        display_name="External locations enumerate without error",
        description=("Confirm data access routes through UC external locations rather than the DBFS root "
                     "by enumerating them. Reachability-only: a clean workspace may have none."),
        operation=dict(service="external_locations", method="list", verb="GET",
                       params={"demo_evidence": {"count": 2}}),
        handler_key="databricks_sdk",
        assertion="the external-locations list call completes without error")),
    ("gov_storage_credentials", dict(
        display_name="Storage credentials enumerate without error",
        description=("Confirm UC storage credentials are reachable. Reachability-only: a clean workspace "
                     "may have none, so this never asserts a non-empty result."),
        operation=dict(service="storage_credentials", method="list", verb="GET",
                       params={"demo_evidence": {"count": 1}}),
        handler_key="databricks_sdk",
        assertion="the storage-credentials list call completes without error")),
    ("gov_grant_read_allowed", dict(
        display_name="Effective grant read is allowed",
        description="The effective identity can read its own grants on the production catalog.",
        operation=dict(service="grants", method="get", verb="GET",
                       params={"securable_type": "catalog", "full_name": "prod",
                               "demo_evidence": {"privileges": ["SELECT"]}}),
        handler_key="databricks_sdk",
        assertion="the effective identity's grant read succeeds")),
    ("gov_grant_read_denied", dict(
        display_name="Ungranted read is denied (fails closed)",
        description=("Counterfactual: a read the effective identity lacks is denied — UC enforcement is "
                     "proven, not assumed."),
        operation=dict(service="grants", method="get", verb="GET",
                       params={"securable_type": "catalog", "full_name": "prod",
                               "demo_outcome": "deny"}),
        handler_key="databricks_sdk",
        assertion="a read the effective identity lacks is denied and fails closed")),
]


TESTS: dict[str, TestSpec] = {
    "governance": TestSpec(
        "Governance / Unity Catalog posture",
        ("A metastore is assigned, the production catalog is ISOLATED, storage credentials "
         "and external locations are reachable, and an ungranted read fails closed."),
        [("gov_metastore_assigned", "assertion"),
         ("gov_catalog_isolated", "assertion"),
         ("gov_external_locations", "assertion"),
         ("gov_storage_credentials", "assertion"),
         ("gov_grant_read_allowed", "assertion"),
         ("gov_grant_read_denied", "counterfactual")],
        ["governance", "uc", "policy", "safety"],
    ),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Governance / Unity Catalog Suite",
             "UC governance posture: metastore, ISOLATED catalog binding, external-location/"
             "storage-credential reachability, and a grant-denied counterfactual.",
             ["governance"], tags=["seeded", "governance", "uc"]),
]


CONTRIBUTION = Contribution(
    check_specs=CHECK_SPECS,
    tests=TESTS,
    suites=SUITES,
    ci_gate_tests=["governance"],
)
