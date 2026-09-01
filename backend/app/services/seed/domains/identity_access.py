"""Identity & Access posture (issue #94), split from the monolithic
real-catalog module (#107).

Three read-only, R0, deterministic reads (``verb="GET"`` derives R0 via
``derive_risk_tier``; the "succeeds"/"row_count >= N" phrasing compiles
deterministic via ``compile_assertion``) plus one agentic advisory Check —
least-privilege drift is judgment, not a hard fail, so its assertion is
deliberately fuzzy ("look reasonable") to route it agentic/advisory.

Split into a deterministic read-only Test and an advisory-only Test (issue
#98) so the CI Gate can compose the former without ever pulling the agentic
least-privilege read along with it — a Suite's gating unit is a whole Test
(SuiteMember), so the two evaluator kinds can't share one Test if either is
to be independently CI-gateable. The Identity & Access Suite below still
carries both, in the same order as before, so its own membership is
unchanged. ``identity_access_readonly`` volunteers for the CI Gate via
``ci_gate_tests``; the advisory Test does not.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

CHECK_SPECS: list[tuple[str, dict]] = [
    ("whoami", dict(
        display_name="Effective identity resolves (who-am-I)",
        description="The platform resolves an effective identity as the calling principal.",
        operation=dict(service="current_user", method="me", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the platform resolves an effective identity: the call succeeds")),
    ("scim_groups_present", dict(
        display_name="SCIM-provisioned groups exist",
        description="Groups are provisioned via SCIM, so access is managed by groups, not ad-hoc users.",
        operation=dict(service="groups", method="list", verb="GET",
                       params={"demo_evidence": {"row_count": 3}}),
        handler_key="databricks_sdk",
        assertion="SCIM-provisioned groups exist (row_count >= 1)")),
    ("ip_access_list_active", dict(
        display_name="IP access list is configured and enabled",
        description="An IP access list is configured and enabled, enforcing network-level access control.",
        operation=dict(service="ip_access_lists", method="list", verb="GET",
                       params={"demo_evidence": {"row_count": 1}}),
        handler_key="databricks_sdk",
        assertion="an IP access list is configured and enabled (row_count >= 1)")),
    ("sp_least_privilege", dict(
        display_name="Service principals are not blanket workspace-admins",
        description="Advisory read of least-privilege drift: service principals should not hold "
                    "blanket workspace-admin membership.",
        operation=dict(service="service_principals", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the service principals' admin-group footprint looks reasonable, not blanket")),
]


TESTS: dict[str, TestSpec] = {
    "identity_access_readonly": TestSpec(
        "Identity & Access posture (read-only)",
        "Effective identity resolves, SCIM groups are provisioned, and an IP access list "
        "is active — the deterministic reads that can gate CI.",
        [("whoami", "assertion"), ("scim_groups_present", "assertion"),
         ("ip_access_list_active", "assertion")],
        ["identity", "access", "security"],
    ),
    "identity_access_advisory": TestSpec(
        "Identity & Access posture (advisory)",
        "Advisory read on service-principal least privilege — judgment, not an "
        "objective pass/fail, so it never gates CI.",
        [("sp_least_privilege", "assertion")],
        ["identity", "access", "security", "advisory"],
    ),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Identity & Access Suite",
             "Effective identity, SCIM-managed groups, and IP access-list posture, plus "
             "an advisory service-principal least-privilege read.",
             ["identity_access_readonly", "identity_access_advisory"],
             tags=["seeded", "identity", "security"]),
]


CONTRIBUTION = Contribution(
    check_specs=CHECK_SPECS,
    tests=TESTS,
    suites=SUITES,
    ci_gate_tests=["identity_access_readonly"],
)
