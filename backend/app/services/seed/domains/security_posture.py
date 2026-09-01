"""Security Posture (issue #93), split from the monolithic real-catalog
module (#107): the platform's SAT-style posture battery — SSO/isolation/
exfiltration/token-hygiene settings Checks plus one deliberately agentic
admin-count Check.

Every settings Check declares ``verb="GET"`` explicitly (R0) and asserts a
specific evidence value via a field-comparison ("field == N"), seeded via
``demo_evidence`` so every Check stays runnable without a workspace binding —
both compile deterministically via ``compile_assertion``. ``admin_count_sane``
is the one exception: an agentic advisory read, non-deterministic by design,
so it stays out of ``ci_gate_tests`` below.

``sso``/``isolation``/``exfiltration``/``token_hygiene`` volunteer for the CI
Gate via ``ci_gate_tests``; ``admin_count`` deliberately does not.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

CHECK_SPECS: list[tuple[str, dict]] = [
    ("sso_enabled", dict(
        display_name="SSO enabled",
        description="Confirm federated single sign-on is enabled for the workspace, "
                    "so authentication doesn't fall back to local passwords.",
        operation=dict(service="workspace_conf", method="get_status", verb="GET",
                       params={"keys": "ssoEnabled", "demo_evidence": {"sso_enabled": 1}}),
        handler_key="databricks_sdk",
        assertion="sso_enabled == 1")),
    ("no_isolation_clusters", dict(
        display_name="No unisolated ('No Isolation Shared') clusters",
        description="Confirm no cluster in the workspace runs under the permissive "
                    "No Isolation Shared access mode, so user isolation is enforced workspace-wide.",
        operation=dict(service="clusters", method="list", verb="GET",
                       params={"demo_evidence": {"no_isolation_clusters": 0}}),
        handler_key="databricks_sdk",
        assertion="no_isolation_clusters == 0")),
    ("exfiltration_controls_off", dict(
        display_name="Exfiltration controls disabled",
        description="Confirm notebook export, results download, and notebook/table clipboard "
                    "copy are all disabled, closing the primary data-exfiltration surface.",
        operation=dict(service="workspace_conf", method="get_status", verb="GET",
                       params={"keys": "enableExportNotebook,enableResultsDownloading,"
                                       "enableNotebookTableClipboard",
                               "demo_evidence": {"exfiltration_controls_enabled": 0}}),
        handler_key="databricks_sdk",
        assertion="exfiltration_controls_enabled == 0")),
    ("pat_expiry_bounded", dict(
        display_name="Personal access tokens are expiry-bounded",
        description="Confirm no personal access token is non-expiring and a workspace "
                    "max-lifetime policy is set, so a leaked PAT can't be used indefinitely.",
        operation=dict(service="token_management", method="list", verb="GET",
                       params={"demo_evidence": {"non_expiring_pats": 0}}),
        handler_key="databricks_sdk",
        assertion="non_expiring_pats == 0")),
    ("admin_count_sane", dict(
        display_name="Workspace-admin count is sane",
        description="An agentic advisory read: judges whether the number of workspace admins "
                    "looks reasonable for the workspace, rather than an objective pass/fail. "
                    "Non-deterministic by design, so it stays advisory and never gates CI.",
        operation=dict(service="users", method="list", verb="GET",
                       params={"demo_evidence": {"admin_count": 3}}),
        handler_key="databricks_sdk",
        assertion="the workspace-admin count is sane")),
]


TESTS: dict[str, TestSpec] = {
    "sso": TestSpec("SSO enforcement", "Federated single sign-on is enabled.",
                    [("sso_enabled", "assertion")], ["security", "identity", "posture"]),
    "isolation": TestSpec("User isolation enforcement",
                          "No cluster runs under the permissive No Isolation Shared mode.",
                          [("no_isolation_clusters", "assertion")], ["security", "compute", "posture"]),
    "exfiltration": TestSpec("Exfiltration controls",
                             "Notebook export, results download, and clipboard copy are off.",
                             [("exfiltration_controls_off", "assertion")], ["security", "posture"]),
    "token_hygiene": TestSpec("Token hygiene",
                              "No non-expiring PATs; a max-lifetime policy is set.",
                              [("pat_expiry_bounded", "assertion")], ["security", "identity", "posture"]),
    "admin_count": TestSpec("Workspace-admin count (advisory)",
                            "Judgement read on whether the admin count looks sane.",
                            [("admin_count_sane", "assertion")], ["security", "posture", "advisory"]),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Security Posture Suite",
             "SSO, user isolation, exfiltration controls, and token hygiene — "
             "plus an advisory read on the workspace-admin count.",
             ["sso", "isolation", "exfiltration", "token_hygiene", "admin_count"],
             tags=["seeded", "security", "posture"]),
]


CONTRIBUTION = Contribution(
    check_specs=CHECK_SPECS,
    tests=TESTS,
    suites=SUITES,
    ci_gate_tests=["sso", "isolation", "exfiltration", "token_hygiene"],
)
