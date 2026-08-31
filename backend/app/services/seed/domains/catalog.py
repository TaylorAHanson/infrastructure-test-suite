"""Real posture catalog (Spec #91) — the distinct home later slices extend.

Same shape as the tutorial domain's ``CHECK_SPECS``: (key, CheckCreate
kwargs), seeded through the identical ``build_check_fields()`` path
(:func:`app.services.seed.build.make_check`), so every entry a future slice
appends here inherits the same compiler-derived guarantees (evaluator, risk
tier, R3 refusal) as the tutorial exemplars — but without ``TUTORIAL_TAGS``,
since this becomes the headline catalog, not the worked examples.

Slices landed so far: Producer/Processing/Consumer posture reads (#96),
Identity & Access (#94, including one deliberately agentic/advisory
least-privilege Check), Governance/Unity Catalog (#95, including its
grant-denied counterfactual pair), Security Posture (#93, the platform's
SAT-style posture battery: SSO/isolation/exfiltration/token-hygiene settings
Checks plus one deliberately agentic admin-count Check), and Sandbox
Functional (#97, the catalog's one mutating validation: create a trivial
Job, run it, confirm it produces output, then delete it via cleanup). Every
read-only entry declares ``verb="GET"`` explicitly (R0) and phrases its
assertion as bare success ("... succeeds" / "... completes without error")
or a field-comparison ("field == N") — both compile deterministically via
``compile_assertion``, so every non-advisory Check here still gates CI. The
bare-success/"without error" form is the reachability shape for a resource
that can legitimately be empty on a clean workspace (a fresh install has
zero DLT pipelines, cluster policies, secret scopes, external locations,
...; asserting non-emptiness there would false-fail); the field-comparison
form is for settings Checks that assert a specific evidence value, seeded via
``demo_evidence`` so every Check stays runnable without a workspace binding.
The job-smoke Check (#97) is the one exception to "read-only": it declares
a ``confirmed_risk_tier`` of R2 (ADR-0017 §3's one-confirm operation-specific
tier), since its create+run+delete lifecycle is a full mutating round-trip
the coarse verb guess (submit -> POST/R1) would under-classify — the safety
gate then caps it to sandbox targets only.
The CI Gate (#98) adds no new Checks — it recomposes the deterministic
Tests already wired from these slices below (Security Posture + Identity &
Access + Governance/UC), so it stays deterministic_only and prod-safe.

The Producer/Processing/Consumer Suites below name Test keys the tutorial
domain module owns (``producer``, ``processing``, ``policy``, ``sql``,
``uc``, ``egress``) alongside this module's own posture Tests — the
cross-module Suite membership the registry resolves once every domain's
Checks/Tests are merged (#106).
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

CATALOG_CHECK_SPECS: list[tuple[str, dict]] = [
    # --- Producer: ingestion layer -------------------------------------------
    ("dlt_pipeline_target_declared", dict(
        display_name="DLT pipeline declares a target catalog/schema",
        description="List Delta Live Tables pipelines to confirm the ingestion layer's pipeline "
                    "definitions (which declare a target catalog/schema) are reachable. A clean "
                    "workspace may have zero pipelines — reachability only, no false-fail.",
        operation=dict(service="pipelines", method="list_pipelines", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the pipeline list without error")),
    # --- Processing: compute policy + serverless SQL -------------------------
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
    # --- Consumer: secrets + model serving ------------------------------------
    ("secret_scopes_exist", dict(
        display_name="Secret scopes exist (credentials aren't hardcoded)",
        description="List Databricks secret scopes to confirm credentials are stored in scopes, "
                    "not hardcoded. A clean workspace may have zero scopes — reachability only, "
                    "no false-fail.",
        operation=dict(service="secrets", method="list_scopes", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the secret scope list without error")),
    ("model_serving_enumerates", dict(
        display_name="Model-serving layer enumerates without error",
        description="List Model Serving endpoints to confirm the serving layer is reachable. A "
                    "clean workspace may have zero endpoints — reachability only, no false-fail.",
        operation=dict(service="serving_endpoints", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the model-serving endpoint list without error")),
    # --- Identity & Access posture (issue #94) ------------------------------
    # Three read-only, R0, deterministic reads (verb="GET" derives R0 via
    # derive_risk_tier; the "succeeds"/"row_count >= N" phrasing compiles
    # deterministic via compile_assertion) plus one agentic advisory Check —
    # least-privilege drift is judgment, not a hard fail, so its assertion is
    # deliberately fuzzy ("look reasonable") to route it agentic/advisory.
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
    # --- Governance / Unity Catalog posture (#95) -----------------------
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
    # --- Security Posture (issue #93) ----------------------------------------
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
    # --- Sandbox Functional (#97): the catalog's one mutating validation ----
    ("job_smoke", dict(
        display_name="Job create/run/delete smoke",
        description=(
            "Submit a trivial one-time Databricks Job run, confirm it completes "
            "and produces output, then delete the run via the generic handler's "
            "cleanup hook. Confirmed R2: a create+run+delete lifecycle is a full "
            "mutating round-trip that the coarse verb guess (submit -> POST/R1) "
            "would under-classify (ADR-0017 §2c/3) — the confirmation keeps this "
            "sandbox-only under the safety-class cap matrix (ADR-0004)."
        ),
        operation=dict(
            service="jobs", method="submit",
            params={
                "run_name": "infra-test-suite-job-smoke",
                "tasks": [{"task_key": "smoke",
                          "notebook_task": {"notebook_path": "/Shared/job-smoke"}}],
                "demo_evidence": {"run_id": "demo-run-1", "output": "job-smoke ok"},
                "cleanup": {"service": "jobs", "method": "delete_run",
                           "params": {"run_id": "$response.run_id"}},
            }),
        handler_key="databricks_sdk",
        assertion="the job run completes and produces output",
        confirmed_risk_tier="R2")),
]


# Real posture Tests (Spec #91). No TUTORIAL_TAGS — these are headline catalog.
TESTS: dict[str, TestSpec] = {
    # --- Real posture Tests (issue #96) -------------------------
    # No tutorial tags — these wire CATALOG_CHECK_SPECS checks into the
    # existing lane Suites below, alongside (not instead of) the tutorial
    # Tests those Suites already carry.
    "producer_posture": TestSpec(
        "Producer ingestion posture",
        "DLT pipelines are reachable and declare a target catalog/schema.",
        [("dlt_pipeline_target_declared", "assertion")],
        ["producer", "ingestion", "posture"],
    ),
    "processing_posture": TestSpec(
        "Processing compute posture",
        "Cluster policies enforce auto-termination + pin instance types/runtime; "
        "serverless SQL is enabled where expected.",
        [("cluster_policy_auto_termination", "assertion"),
         ("cluster_policy_instance_runtime", "assertion"),
         ("serverless_sql_enabled", "assertion")],
        ["processing", "compute", "policy", "posture"],
    ),
    "consumer_posture": TestSpec(
        "Consumer security posture",
        "Secret scopes exist (no hardcoded credentials) and the model-serving "
        "layer enumerates without error.",
        [("secret_scopes_exist", "assertion"),
         ("model_serving_enumerates", "assertion")],
        ["consumer", "security", "posture"],
    ),
    # --- Real posture Tests (issue #94) -------------------------
    # Split into a deterministic read-only Test and an advisory-only Test
    # (issue #98) so the CI Gate can compose the former without ever
    # pulling the agentic least-privilege read along with it — a Suite's
    # gating unit is a whole Test (SuiteMember), so the two evaluator
    # kinds can't share one Test if either is to be independently
    # CI-gateable. The Identity & Access Suite below still carries both,
    # in the same order as before, so its own membership is unchanged.
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
    # --- Real posture Tests (issue #95) -------------------------
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
    # --- Real posture Tests (issue #93) -------------------------
    # Security Posture. One Test per Check — headline catalog, no tutorial tags.
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
    "job_smoke": TestSpec(
        "Job create/run/delete smoke",
        "Create, run, and delete a trivial job — proves the ingestion layer "
        "actually executes, not just reachable (sandbox-only; cleans up after itself).",
        [("job_smoke", "assertion")],
        ["sandbox", "producer", "functional"],
    ),
}


SUITES: list[SuiteSpec] = [
    # Category suites — one per Producer/Processing/Consumer lane (README §2).
    # Each grows in place with its real posture Test (issue #96) alongside the
    # tutorial Test it already carried (owned by the tutorial domain module) —
    # the lane stays coherent either way.
    SuiteSpec("Producer Suite", "Ingestion and pipeline layer reachability.",
             ["producer", "producer_posture"], tags=["seeded", "producer"]),
    SuiteSpec("Processing Suite", "Compute and policy layer validation.",
             ["processing", "policy", "processing_posture"], tags=["seeded", "processing"]),
    SuiteSpec("Consumer Suite", "SQL access, UC governance, and egress paths.",
             ["sql", "uc", "egress", "consumer_posture"], tags=["seeded", "consumer"]),
    SuiteSpec("Identity & Access Suite",
             "Effective identity, SCIM-managed groups, and IP access-list posture, plus "
             "an advisory service-principal least-privilege read.",
             ["identity_access_readonly", "identity_access_advisory"],
             tags=["seeded", "identity", "security"]),
    SuiteSpec("Governance / Unity Catalog Suite",
             "UC governance posture: metastore, ISOLATED catalog binding, external-location/"
             "storage-credential reachability, and a grant-denied counterfactual.",
             ["governance"], tags=["seeded", "governance", "uc"]),
    # Security Posture (issue #93) is the platform's SAT-style posture
    # battery: read-only/R0 settings Checks plus one agentic advisory Check,
    # bundled together — a fresh install's first real validation battery, not
    # a demo. The lane Suites above (Producer/Processing/Consumer) also grow
    # in place as their slices land, without disturbing the tutorial Tests
    # they already carry.
    SuiteSpec("Security Posture Suite",
             "SSO, user isolation, exfiltration controls, and token hygiene — "
             "plus an advisory read on the workspace-admin count.",
             ["sso", "isolation", "exfiltration", "token_hygiene", "admin_count"],
             tags=["seeded", "security", "posture"]),
    # CI Gate (issue #98): reworked from a demo grouping into the real,
    # deterministic-only, read-only posture gate — its membership is exactly
    # the deterministic Tests from Security Posture + Identity & Access +
    # Governance/UC above, i.e. every objective read those Suites carry,
    # minus each Suite's agentic/advisory Test (ADR-0008 deterministic-only
    # gate). No mutating Check (e.g. Sandbox Functional's job-smoke) is a
    # member of any source Suite here, so none can leak in transitively.
    SuiteSpec("CI Gate",
             "Deterministic-only, read-only posture gate for the CI trigger: the "
             "objective reads from Security Posture, Identity & Access, and "
             "Governance / Unity Catalog — never a judgment read, never a mutation.",
             ["sso", "isolation", "exfiltration", "token_hygiene",
              "identity_access_readonly", "governance"],
             deterministic_only=True, tags=["seeded", "ci"]),
    # Sandbox Functional (#97): the catalog's mutating Checks, fenced off from
    # every deterministic-only/CI-gated Suite above — never listed in CI Gate.
    SuiteSpec("Sandbox Functional", "Mutating validations confined to sandbox targets, with automatic cleanup.",
             ["job_smoke"], tags=["seeded", "sandbox", "functional"]),
]


CONTRIBUTION = Contribution(
    check_specs=CATALOG_CHECK_SPECS,
    tests=TESTS,
    suites=SUITES,
)
