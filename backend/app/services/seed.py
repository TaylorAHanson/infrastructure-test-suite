"""Seed data: Check/Test/Suite exemplars so the app is useful on first boot.

Idempotent: if any Check exists, seeding is skipped. The exemplars are a
worked-example library (ADR-0006) covering the shapes the model must express:
a deterministic connectivity Check, a functional (row-count) Check, an
open-SDK mutating Check with a **counterfactual** pair, and an **agentic**
(non-compilable → advisory) Check.
"""
from __future__ import annotations

import logging

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


# (key, CheckCreate kwargs). ``key`` is a local handle used to wire Tests below.
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


def _make_check(spec: dict) -> Check:
    return Check(**build_check_fields(CheckCreate(**spec)))


def _seed_examples(session: Session) -> int:
    """Seed the worked-example library (idempotent within a fresh seed)."""
    if session.exec(select(WorkedExample)).first():
        return 0
    for spec in WORKED_EXAMPLES:
        session.add(WorkedExample(
            intent=spec["intent"], title=spec["title"], description=spec["intent"],
            operation=spec["operation"], handler_key=spec["handler_key"],
            assertion=spec["assertion"], expectation=Expectation.PASS,
            counterfactual=spec.get("counterfactual", {}), tags=spec["tags"],
            curated=True,
        ))
    session.commit()
    return len(WORKED_EXAMPLES)


def seed_all(session: Session, with_demo_targets: bool = True, **_ignored) -> None:
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
        check = _make_check(spec)
        session.add(check)
        checks[key] = check
    session.commit()
    for check in checks.values():
        session.refresh(check)

    # Tests: target-agnostic bundles, one carrying a counterfactual pair.
    def make_test(name: str, description: str, members: list[tuple[str, str]],
                  tags: list[str]) -> Test:
        test = Test(name=name, description=description, tags=tags)
        session.add(test)
        session.commit()
        session.refresh(test)
        for i, (check_key, kind) in enumerate(members):
            counterpart = checks["cluster_create_allowed"].id if kind == "counterfactual" else None
            session.add(TestCheck(test_id=test.id, check_id=checks[check_key].id, sort_order=i,
                                  kind=kind, counterpart_check_id=counterpart))
        session.commit()
        return test

    tests = {
        "sql": make_test("SQL Warehouse validation", "Warehouse reachable + returns rows.",
                         [("sql_healthcheck", "assertion")], ["sql", "connectivity"]),
        "uc": make_test("Unity Catalog access", "UC access via the SQL warehouse.",
                        [("uc_listing", "assertion")], ["uc", "governance"]),
        "egress": make_test("Egress connectivity", "Outbound HTTPS egress works.",
                            [("egress_example", "assertion")], ["consumer", "egress"]),
        "policy": make_test("Cluster policy enforcement", "Compliant allowed; non-compliant denied.",
                            [("cluster_create_allowed", "assertion"),
                             ("cluster_create_denied", "counterfactual")], ["policy", "safety"]),
        "self": make_test("Platform self-check", "The app's own store + a health read.",
                          [("lakebase_txn", "assertion"),
                           ("warehouse_healthy_ish", "assertion")], ["platform"]),
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

    logger.info("Seed complete: %d checks, %d tests, %d suites.",
                len(checks), len(tests), 3)
