"""Seed data: Check/Test/Suite exemplars so the app is useful on first boot.

Idempotent: if any Check exists, seeding is skipped. The exemplars are a
**tutorial/worked-example library** (ADR-0006, demoted by Spec #91) covering
the authoring shapes the model must express: a deterministic connectivity
Check, a functional (row-count) Check, an open-SDK mutating Check with a
**counterfactual** pair, and an **agentic** (non-compilable → advisory)
Check. They carry :data:`TUTORIAL_TAGS` so they're distinguishable from the
real posture catalog (:data:`CATALOG_CHECK_SPECS`) that later slices of
Spec #91 add alongside them, without disturbing the exemplars.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from app.core.enums import Expectation, SafetyClass
from app.models import (
    Check,
    Suite,
    SuiteMember,
    Target,
    Test,
    TestCheck,
    WorkedExample,
)
from app.schemas.schemas import CheckCreate, OperationIn
from app.services.authoring import build_check_fields

logger = logging.getLogger(__name__)


# Applied (via ``hints["tags"]`` on Checks, ``tags`` on Tests) to every exemplar
# below so a fresh boot's catalog is clearly labelled as tutorial content, not
# the operator's real validation battery (Spec #91).
TUTORIAL_TAGS: list[str] = ["tutorial", "example"]


# (key, CheckCreate kwargs). ``key`` is a local handle used to wire Tests below.
# Tutorial/worked-example Checks only — see CATALOG_CHECK_SPECS below for the
# real posture catalog's home.
CHECK_SPECS: list[tuple[str, dict]] = [
    ("sql_healthcheck", dict(
        display_name="SQL warehouse healthcheck",
        description="Run a trivial statement on a SQL warehouse and confirm it returns a row.",
        # A read-only SELECT: declare verb=GET so the coarse heuristic doesn't
        # read "execute_statement" as a mutation (ADR-0017 — the author resolves
        # the verb the heuristic can't infer from an SDK method name).
        operation=OperationIn(service="statement_execution", method="execute_statement", verb="GET",
                              params={"statement": "SELECT 1 AS healthcheck", "demo_row_count": 1}),
        handler_key="sql_warehouse",
        assertion="the query returns at least one row")),
    ("uc_listing", dict(
        display_name="Unity Catalog listing",
        description="List catalogs via the SQL warehouse to prove UC access.",
        operation=OperationIn(service="catalogs", method="list",
                              params={"statement": "SHOW CATALOGS", "demo_row_count": 3}),
        handler_key="sql_warehouse",
        assertion="the query returns at least one row")),
    ("egress_example", dict(
        display_name="Outbound egress to example.com",
        description="Prove the app egress path can reach an external HTTPS endpoint.",
        operation=OperationIn(service="http", method="get",
                              params={"url": "https://example.com", "demo_status_code": 200}),
        handler_key="outbound_http",
        assertion="HTTP status is 200")),
    ("cluster_create_allowed", dict(
        display_name="Cluster create is allowed",
        description="A compliant cluster create succeeds under the workspace policy.",
        operation=OperationIn(service="clusters", method="create", params={}),
        handler_key="databricks_sdk",
        assertion="the operation succeeds")),
    ("cluster_create_denied", dict(
        display_name="Non-compliant cluster create is denied",
        description="A non-compliant cluster create must be denied (fails closed).",
        operation=OperationIn(service="clusters", method="create", params={"demo_outcome": "deny"}),
        handler_key="databricks_sdk",
        assertion="a non-compliant cluster is denied by policy")),
    ("lakebase_txn", dict(
        display_name="Lakebase transaction",
        description="Validate the app's own operational store with a minimal transaction.",
        operation=OperationIn(service="lakebase", method="query", params={}),
        handler_key="lakebase",
        assertion="the transaction succeeds")),
    ("warehouse_healthy_ish", dict(
        display_name="Warehouse came up healthy-ish",
        description="A subjective health read that can only run advisory (agentic).",
        operation=OperationIn(service="statement_execution", method="execute_statement", verb="GET",
                              params={"statement": "SELECT 1", "demo_row_count": 1}),
        handler_key="sql_warehouse",
        assertion="the warehouse came up healthy-ish")),
    # --- Producer-category checks -------------------------------------------
    ("jobs_list", dict(
        display_name="Databricks Jobs list",
        description="List Databricks Jobs to confirm the batch ingestion / workflow layer is reachable.",
        operation=OperationIn(service="jobs", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the job list without error")),
    ("pipelines_list", dict(
        display_name="Delta Live Tables pipeline list",
        description="List DLT pipelines to confirm the streaming/ETL ingestion layer is accessible.",
        operation=OperationIn(service="pipelines", method="list_pipelines", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the pipeline list without error")),
    # --- Processing-category checks -----------------------------------------
    ("warehouses_list", dict(
        display_name="SQL warehouse inventory",
        description="List all SQL warehouses to confirm the compute processing tier can be enumerated.",
        operation=OperationIn(service="warehouses", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        # Reachability assertion only — a fresh workspace may have zero warehouses,
        # so asserting a non-empty result would false-fail a clean install.
        assertion="the call returns the warehouse list without error")),
]


# ---------------------------------------------------------------------------
# Real posture catalog (Spec #91) — the distinct home later slices extend.
#
# Same shape as CHECK_SPECS above: (key, CheckCreate kwargs), seeded through
# the identical build_check_fields() path (_make_check), so every entry a
# future slice appends here inherits the same compiler-derived guarantees
# (evaluator, risk tier, R3 refusal) as the tutorial exemplars — but WITHOUT
# TUTORIAL_TAGS, since this becomes the headline catalog, not the worked
# examples. Real-catalog Tests/Suites are wired the same way further down
# (see the matching comment markers in seed_all): append to the ``tests``
# dict / call make_suite() without passing ``tutorial=True``.
#
# Slices landed so far: Producer/Processing/Consumer posture reads (#96),
# Identity & Access (#94, including one deliberately agentic/advisory
# least-privilege Check), and Governance/Unity Catalog (#95, including its
# grant-denied counterfactual pair). Every read-only entry declares
# ``verb="GET"`` explicitly (R0) and phrases its assertion as bare success
# ("... succeeds" / "... completes without error") — the reachability form for
# a resource that can legitimately be empty on a clean workspace (a fresh
# install has zero DLT pipelines, cluster policies, secret scopes, external
# locations, ...; asserting non-emptiness there would false-fail). That phrase
# compiles deterministically via ``compile_assertion`` (a bare "ok" predicate),
# so every non-advisory Check here still gates CI. Security Posture, a
# reworked CI Gate, and Sandbox Functional (#91's remaining suggested phasing)
# are later slices, appended here without disturbing these.
# ---------------------------------------------------------------------------
CATALOG_CHECK_SPECS: list[tuple[str, dict]] = [
    # --- Producer: ingestion layer -------------------------------------------
    ("dlt_pipeline_target_declared", dict(
        display_name="DLT pipeline declares a target catalog/schema",
        description="List Delta Live Tables pipelines to confirm the ingestion layer's pipeline "
                    "definitions (which declare a target catalog/schema) are reachable. A clean "
                    "workspace may have zero pipelines — reachability only, no false-fail.",
        operation=OperationIn(service="pipelines", method="list_pipelines", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the pipeline list without error")),
    # --- Processing: compute policy + serverless SQL -------------------------
    ("cluster_policy_auto_termination", dict(
        display_name="Cluster policies enforce auto-termination",
        description="List cluster policies to confirm the compute layer's policies (which pin "
                    "auto-termination) are reachable. A clean workspace may have zero custom "
                    "policies — reachability only, no false-fail.",
        operation=OperationIn(service="cluster_policies", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the cluster policy list without error")),
    ("cluster_policy_instance_runtime", dict(
        display_name="Cluster policies pin approved instance types and runtime",
        description="List cluster policies to confirm the compute layer's policies (which pin "
                    "approved instance types + Databricks Runtime) are reachable. A clean "
                    "workspace may have zero custom policies — reachability only, no false-fail.",
        operation=OperationIn(service="cluster_policies", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the cluster policy list without error")),
    ("serverless_sql_enabled", dict(
        display_name="Serverless SQL is enabled where expected",
        description="List SQL warehouses to confirm the serverless-capable compute tier is "
                    "reachable. A clean workspace may have zero warehouses — reachability only, "
                    "no false-fail.",
        operation=OperationIn(service="warehouses", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the warehouse list without error")),
    # --- Consumer: secrets + model serving ------------------------------------
    ("secret_scopes_exist", dict(
        display_name="Secret scopes exist (credentials aren't hardcoded)",
        description="List Databricks secret scopes to confirm credentials are stored in scopes, "
                    "not hardcoded. A clean workspace may have zero scopes — reachability only, "
                    "no false-fail.",
        operation=OperationIn(service="secrets", method="list_scopes", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the secret scope list without error")),
    ("model_serving_enumerates", dict(
        display_name="Model-serving layer enumerates without error",
        description="List Model Serving endpoints to confirm the serving layer is reachable. A "
                    "clean workspace may have zero endpoints — reachability only, no false-fail.",
        operation=OperationIn(service="serving_endpoints", method="list", verb="GET", params={}),
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
        operation=OperationIn(service="current_user", method="me", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the platform resolves an effective identity: the call succeeds")),
    ("scim_groups_present", dict(
        display_name="SCIM-provisioned groups exist",
        description="Groups are provisioned via SCIM, so access is managed by groups, not ad-hoc users.",
        operation=OperationIn(service="groups", method="list", verb="GET",
                              params={"demo_evidence": {"row_count": 3}}),
        handler_key="databricks_sdk",
        assertion="SCIM-provisioned groups exist (row_count >= 1)")),
    ("ip_access_list_active", dict(
        display_name="IP access list is configured and enabled",
        description="An IP access list is configured and enabled, enforcing network-level access control.",
        operation=OperationIn(service="ip_access_lists", method="list", verb="GET",
                              params={"demo_evidence": {"row_count": 1}}),
        handler_key="databricks_sdk",
        assertion="an IP access list is configured and enabled (row_count >= 1)")),
    ("sp_least_privilege", dict(
        display_name="Service principals are not blanket workspace-admins",
        description="Advisory read of least-privilege drift: service principals should not hold "
                    "blanket workspace-admin membership.",
        operation=OperationIn(service="service_principals", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the service principals' admin-group footprint looks reasonable, not blanket")),
    # --- Governance / Unity Catalog posture (#95) -----------------------
    ("gov_metastore_assigned", dict(
        display_name="Unity Catalog metastore is assigned",
        description="Confirm a Unity Catalog metastore is assigned to the workspace — governance is in force.",
        operation=OperationIn(service="metastores", method="current", verb="GET",
                              params={"demo_evidence": {"metastore_id": "demo-metastore-001"}}),
        handler_key="databricks_sdk",
        assertion="the workspace's metastore-assignment lookup succeeds")),
    ("gov_catalog_isolated", dict(
        display_name="Production catalog is bound ISOLATED",
        description="Confirm the production catalog is bound in ISOLATED mode — no cross-environment access.",
        operation=OperationIn(service="catalogs", method="get", verb="GET",
                              params={"name": "prod",
                                      "demo_evidence": {"isolation_mode": "ISOLATED"}}),
        handler_key="databricks_sdk",
        assertion="the production catalog lookup succeeds and reports ISOLATED isolation mode")),
    ("gov_external_locations", dict(
        display_name="External locations enumerate without error",
        description=("Confirm data access routes through UC external locations rather than the DBFS root "
                     "by enumerating them. Reachability-only: a clean workspace may have none."),
        operation=OperationIn(service="external_locations", method="list", verb="GET",
                              params={"demo_evidence": {"count": 2}}),
        handler_key="databricks_sdk",
        assertion="the external-locations list call completes without error")),
    ("gov_storage_credentials", dict(
        display_name="Storage credentials enumerate without error",
        description=("Confirm UC storage credentials are reachable. Reachability-only: a clean workspace "
                     "may have none, so this never asserts a non-empty result."),
        operation=OperationIn(service="storage_credentials", method="list", verb="GET",
                              params={"demo_evidence": {"count": 1}}),
        handler_key="databricks_sdk",
        assertion="the storage-credentials list call completes without error")),
    ("gov_grant_read_allowed", dict(
        display_name="Effective grant read is allowed",
        description="The effective identity can read its own grants on the production catalog.",
        operation=OperationIn(service="grants", method="get", verb="GET",
                              params={"securable_type": "catalog", "full_name": "prod",
                                      "demo_evidence": {"privileges": ["SELECT"]}}),
        handler_key="databricks_sdk",
        assertion="the effective identity's grant read succeeds")),
    ("gov_grant_read_denied", dict(
        display_name="Ungranted read is denied (fails closed)",
        description=("Counterfactual: a read the effective identity lacks is denied — UC enforcement is "
                     "proven, not assumed."),
        operation=OperationIn(service="grants", method="get", verb="GET",
                              params={"securable_type": "catalog", "full_name": "prod",
                                      "demo_outcome": "deny"}),
        handler_key="databricks_sdk",
        assertion="a read the effective identity lacks is denied and fails closed")),
]


# The repo-seeded worked-example library (ADR-0006 §9): gold Tests the Assistant
# grounds on so an open-source install has a working assistant on day one.
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


# Curated **onboarding** worked examples (spec #56, user stories 30/32): the
# starter prompts a first-time, untrained user sees when they open the Assistant
# with an empty conversation. Deliberately read-only, single-sentence intents
# that make the authoring flow succeed on the first click — distinct from the
# default Test/Suite catalog content. They carry the ``onboarding`` tag so the
# shelf can surface exactly these via the existing ``search_examples`` path
# (``GET /assistant/examples?q=onboarding``).
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


def _make_check(spec: dict, *, tags: list[str] | None = None) -> Check:
    """Build a Check from a spec, optionally stamping ``hints["tags"]``.

    Check has no dedicated tags column (unlike Test/Suite); ``hints`` is the
    existing free-form grounding-metadata field, so tutorial/example tagging
    rides there rather than adding a migration for this prefactor.
    """
    if tags:
        spec = {**spec, "hints": {**spec.get("hints", {}), "tags": tags}}
    return Check(**build_check_fields(CheckCreate(**spec)))


def _seed_examples(session: Session) -> int:
    """Seed the worked-example library (idempotent within a fresh seed).

    Seeds the gold authoring examples **plus** the curated onboarding starter
    prompts (:data:`ONBOARDING_EXAMPLES`) — both live in the same library and
    are grounded/surfaced through the same ``search_examples`` path.
    """
    if session.exec(select(WorkedExample)).first():
        return 0
    specs = WORKED_EXAMPLES + ONBOARDING_EXAMPLES
    for spec in specs:
        session.add(WorkedExample(
            intent=spec["intent"], title=spec["title"], description=spec["intent"],
            operation=spec["operation"], handler_key=spec["handler_key"],
            assertion=spec["assertion"], expectation=Expectation.PASS,
            counterfactual=spec.get("counterfactual", {}), tags=spec["tags"],
            curated=True,
        ))
    session.commit()
    return len(specs)


def seed_all(session: Session, with_demo_targets: bool = True) -> None:
    """Seed Check/Test/Suite exemplars (idempotent)."""
    # Branding is seeded independently of the catalog so an un-branded install
    # always has the finished default record (ADR-0013 §5), even once the catalog
    # already exists. Idempotent by the fixed singleton id.
    from app.services.branding import get_or_seed as seed_branding

    seed_branding(session)

    if session.exec(select(Check)).first():
        logger.info("Seed skipped: checks already present.")
        return

    _seed_examples(session)  # worked-example library (ADR-0006 §9)
    logger.info("Seeding Check/Test/Suite exemplars...")
    checks: dict[str, Check] = {}
    for key, spec in CHECK_SPECS:
        check = _make_check(spec, tags=TUTORIAL_TAGS)
        session.add(check)
        checks[key] = check
    # Real posture catalog (Spec #91) — empty today; a future slice's entries
    # land here, in the same ``checks`` dict, minted without TUTORIAL_TAGS.
    for key, spec in CATALOG_CHECK_SPECS:
        check = _make_check(spec)
        session.add(check)
        checks[key] = check
    session.commit()
    for check in checks.values():
        session.refresh(check)

    # Tests: target-agnostic bundles, one carrying a counterfactual pair.
    # ``tutorial=True`` stamps TUTORIAL_TAGS; later slices adding real posture
    # Tests to this same dict call make_test(..., tutorial=False) (the default).
    def make_test(name: str, description: str, members: list[tuple[str, str]],
                  tags: list[str], tutorial: bool = False) -> Test:
        test = Test(name=name, description=description,
                    tags=[*tags, *TUTORIAL_TAGS] if tutorial else tags)
        session.add(test)
        session.commit()
        session.refresh(test)
        # A counterfactual's counterpart is the nearest preceding assertion-kind
        # member in this same members list — the positive Check it negates.
        last_assertion_key: Optional[str] = None
        for i, (check_key, kind) in enumerate(members):
            counterpart = checks[last_assertion_key].id if kind == "counterfactual" and last_assertion_key else None
            session.add(TestCheck(test_id=test.id, check_id=checks[check_key].id, sort_order=i,
                                  kind=kind, counterpart_check_id=counterpart))
            if kind == "assertion":
                last_assertion_key = check_key
        session.commit()
        return test

    tests = {
        "sql": make_test("SQL Warehouse validation", "Warehouse reachable + returns rows.",
                         [("sql_healthcheck", "assertion")], ["sql", "consumer", "connectivity"],
                         tutorial=True),
        "uc": make_test("Unity Catalog access", "UC access via the SQL warehouse.",
                        [("uc_listing", "assertion")], ["uc", "consumer", "governance"],
                        tutorial=True),
        "egress": make_test("Egress connectivity", "Outbound HTTPS egress works.",
                            [("egress_example", "assertion")], ["consumer", "egress"],
                            tutorial=True),
        "policy": make_test("Cluster policy enforcement", "Compliant allowed; non-compliant denied.",
                            [("cluster_create_allowed", "assertion"),
                             ("cluster_create_denied", "counterfactual")], ["processing", "policy", "safety"],
                            tutorial=True),
        "self": make_test("Platform self-check", "The app's own store + a health read.",
                          [("lakebase_txn", "assertion"),
                           ("warehouse_healthy_ish", "assertion")], ["platform"],
                          tutorial=True),
        # --- Producer-category tests ----------------------------------------
        "producer": make_test(
            "Producer ingestion access",
            "Databricks Jobs and DLT pipelines are reachable — the ingestion layer is up.",
            [("jobs_list", "assertion"), ("pipelines_list", "assertion")],
            ["producer", "ingestion", "connectivity"],
            tutorial=True,
        ),
        # --- Processing-category tests --------------------------------------
        "processing": make_test(
            "Processing layer access",
            "SQL warehouses can be enumerated — the compute processing tier is reachable.",
            [("warehouses_list", "assertion")],
            ["processing", "compute", "connectivity"],
            tutorial=True,
        ),
        # --- Real posture Tests (Spec #91, issue #96) -------------------------
        # No tutorial=True — these wire CATALOG_CHECK_SPECS checks into the
        # existing lane Suites below, alongside (not instead of) the tutorial
        # Tests those Suites already carry.
        "producer_posture": make_test(
            "Producer ingestion posture",
            "DLT pipelines are reachable and declare a target catalog/schema.",
            [("dlt_pipeline_target_declared", "assertion")],
            ["producer", "ingestion", "posture"],
        ),
        "processing_posture": make_test(
            "Processing compute posture",
            "Cluster policies enforce auto-termination + pin instance types/runtime; "
            "serverless SQL is enabled where expected.",
            [("cluster_policy_auto_termination", "assertion"),
             ("cluster_policy_instance_runtime", "assertion"),
             ("serverless_sql_enabled", "assertion")],
            ["processing", "compute", "policy", "posture"],
        ),
        "consumer_posture": make_test(
            "Consumer security posture",
            "Secret scopes exist (no hardcoded credentials) and the model-serving "
            "layer enumerates without error.",
            [("secret_scopes_exist", "assertion"),
             ("model_serving_enumerates", "assertion")],
            ["consumer", "security", "posture"],
        ),
        # --- Real posture Tests (Spec #91, issue #94) -------------------------
        "identity_access": make_test(
            "Identity & Access posture",
            "Effective identity resolves, SCIM groups are provisioned, and an IP access list "
            "is active — plus an advisory read on service-principal least privilege.",
            [("whoami", "assertion"), ("scim_groups_present", "assertion"),
             ("ip_access_list_active", "assertion"), ("sp_least_privilege", "assertion")],
            ["identity", "access", "security"],
        ),
        # --- Real posture Tests (Spec #91, issue #95) -------------------------
        "governance": make_test(
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

    # Suites: grouping only. CI Gate is deterministic-only (ADR-0008).
    def make_suite(name: str, description: str, test_keys: list[str],
                   deterministic_only: bool = False, tags=None) -> Suite:
        suite = Suite(name=name, description=description, tags=tags or ["seeded"],
                      deterministic_only=deterministic_only)
        session.add(suite)
        session.commit()
        session.refresh(suite)
        for i, tk in enumerate(test_keys):
            session.add(SuiteMember(suite_id=suite.id, test_id=tests[tk].id, sort_order=i))
        session.commit()
        return suite

    make_suite("Connectivity Suite", "Reachability across producer/consumer paths.",
               ["egress", "sql", "uc"])
    make_suite("Full Regression", "Every seeded Test.", list(tests.keys()))
    make_suite("CI Gate", "Deterministic-only gate for the CI trigger.",
               ["sql", "policy"], deterministic_only=True, tags=["seeded", "ci"])
    # Category suites — one per Producer/Processing/Consumer lane (README §2).
    # Each grows in place with its real posture Test (issue #96) alongside the
    # tutorial Test it already carried — the lane stays coherent either way.
    make_suite("Producer Suite", "Ingestion and pipeline layer reachability.",
               ["producer", "producer_posture"], tags=["seeded", "producer"])
    make_suite("Processing Suite", "Compute and policy layer validation.",
               ["processing", "policy", "processing_posture"], tags=["seeded", "processing"])
    make_suite("Consumer Suite", "SQL access, UC governance, and egress paths.",
               ["sql", "uc", "egress", "consumer_posture"], tags=["seeded", "consumer"])
    make_suite("Identity & Access Suite",
               "Effective identity, SCIM-managed groups, and IP access-list posture, plus "
               "an advisory service-principal least-privilege read.",
               ["identity_access"], tags=["seeded", "identity", "security"])
    make_suite("Governance / Unity Catalog Suite",
               "UC governance posture: metastore, ISOLATED catalog binding, external-location/"
               "storage-credential reachability, and a grant-denied counterfactual.",
               ["governance"], tags=["seeded", "governance", "uc"])
    # Remaining real posture Suites (Security Posture, a reworked CI Gate,
    # Sandbox Functional — Spec #91) are added here by later slices via
    # make_suite(), the same helper used above — the lane Suites above
    # (Producer/Processing/Consumer) also grow in place as their slices land,
    # without disturbing the tutorial Tests they already carry.

    if with_demo_targets:
        for t in [
            Target(name="aws-prod (read-only)", workspace_name="aws-prod-workspace",
                   safety_class=SafetyClass.READ_ONLY, tags={"environment": "prod"}),
            Target(name="sandbox-1", workspace_name="sandbox-workspace",
                   safety_class=SafetyClass.SANDBOX, tags={"environment": "sandbox"}),
            Target(name="prod-for-us-1", workspace_name="platform-workspace",
                   safety_class=SafetyClass.PROD_FOR_US, tags={"environment": "internal"}),
        ]:
            session.add(t)
        session.commit()

    from sqlmodel import select as _select
    suite_count = len(session.exec(_select(Suite)).all())
    logger.info("Seed complete: %d checks, %d tests, %d suites.",
                len(checks), len(tests), suite_count)
