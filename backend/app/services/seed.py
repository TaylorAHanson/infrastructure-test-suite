"""Seed data: the full screenshot test catalog + suites, targets, schedules,
and synthetic historical runs so the dashboard renders immediately.

Idempotent: if definitions already exist, seeding is skipped. The catalog is
declared as data (``CATALOG``) - this is the metadata-driven extension point.
"""
from __future__ import annotations

import logging
import random
from datetime import timedelta

from sqlmodel import Session, select

from app.core.enums import (
    AuthMode,
    CleanupPolicy,
    ConnectivityOutcome,
    ExecutionMode,
    ExecutorKind,
    ProbeOutcome,
    RequestSource,
    RootCauseBucket,
    RunStatus,
    StepKind,
    TestCategory,
)
from app.core.ids import utcnow
from app.models import (
    Schedule,
    SuiteMember,
    TestDefinition,
    TestRun,
    TestRunStep,
    TestSuite,
    TestTarget,
)

logger = logging.getLogger(__name__)

ALL_MODES = [ExecutionMode.PREFLIGHT, ExecutionMode.SMOKE, ExecutionMode.FULL]
PREFLIGHT_SMOKE = [ExecutionMode.PREFLIGHT, ExecutionMode.SMOKE]

# (display_name, category, executor_kind, executor_key, tags, target_type,
#  modes, destructive, enterprise_only, params)
CATALOG: list[dict] = [
    # ---------------- Producer / inbound ----------------
    dict(name="Fivetran -> sales_dev.temp", category=TestCategory.PRODUCER, kind=ExecutorKind.DATABRICKS_JOB,
         key="fivetran_landing", tags=["producer", "fivetran", "ingest"], target_type="catalog",
         modes=ALL_MODES, destructive=True, params={"catalog": "sales_dev", "schema": "temp"}),
    dict(name="IICS -> sales_dev.temp", category=TestCategory.PRODUCER, kind=ExecutorKind.DATABRICKS_JOB,
         key="iics_landing", tags=["producer", "iics", "ingest"], target_type="catalog",
         modes=ALL_MODES, destructive=True, params={"catalog": "sales_dev", "schema": "temp"}),
    dict(name="DBX Auto Loader smoke", category=TestCategory.PRODUCER, kind=ExecutorKind.DATABRICKS_JOB,
         key="autoloader_smoke", tags=["producer", "autoloader"], target_type="catalog",
         modes=[ExecutionMode.SMOKE, ExecutionMode.FULL], destructive=True, params={"catalog": "sales_dev"}),
    dict(name="Oracle Federation (on-prem)", category=TestCategory.PRODUCER, kind=ExecutorKind.SQL_WAREHOUSE,
         key="oracle_federation", tags=["producer", "federation", "onprem", "oracle"], target_type="connection",
         modes=PREFLIGHT_SMOKE, destructive=False, params={"query": "SELECT 1"}),
    dict(name="Other inbound connectivity", category=TestCategory.PRODUCER, kind=ExecutorKind.REMOTE_AGENT,
         key="remote_agent", cap="inbound_generic", tags=["producer", "inbound"], target_type="external_system",
         modes=[ExecutionMode.PREFLIGHT], destructive=False, params={"vantage_point": "producer-network"}),

    # ---------------- Processing / in-workspace ----------------
    dict(name="All-purpose compute", category=TestCategory.PROCESSING, kind=ExecutorKind.DATABRICKS_JOB,
         key="all_purpose_compute", tags=["compute", "all-purpose"], target_type="workspace",
         modes=[ExecutionMode.SMOKE], destructive=False, params={}),
    dict(name="Job compute", category=TestCategory.PROCESSING, kind=ExecutorKind.DATABRICKS_JOB,
         key="job_compute", tags=["compute", "job"], target_type="workspace",
         modes=[ExecutionMode.SMOKE], destructive=False, params={}),
    dict(name="Serverless compute", category=TestCategory.PROCESSING, kind=ExecutorKind.DATABRICKS_JOB,
         key="serverless_compute", tags=["compute", "serverless"], target_type="workspace",
         modes=[ExecutionMode.SMOKE], destructive=False, params={}),
    dict(name="SQL Warehouse Pro", category=TestCategory.PROCESSING, kind=ExecutorKind.SQL_WAREHOUSE,
         key="sql_warehouse_smoke", tags=["sql", "warehouse", "pro"], target_type="warehouse",
         modes=ALL_MODES, destructive=False, params={"query": "SELECT 1 AS healthcheck"}),
    dict(name="SQL Warehouse Serverless", category=TestCategory.PROCESSING, kind=ExecutorKind.SQL_WAREHOUSE,
         key="sql_warehouse_smoke", tags=["sql", "warehouse", "serverless"], target_type="warehouse",
         modes=ALL_MODES, destructive=False, params={"query": "SELECT 1 AS healthcheck"}),
    dict(name="Model Serving endpoint", category=TestCategory.PROCESSING, kind=ExecutorKind.MODEL_SERVING,
         key="model_serving_invoke", tags=["ml", "serving"], target_type="endpoint",
         modes=PREFLIGHT_SMOKE, destructive=False, params={}),
    dict(name="Databricks Apps (sibling health)", category=TestCategory.PROCESSING, kind=ExecutorKind.APP_INLINE,
         key="databricks_app_health", tags=["apps", "oauth"], target_type="app",
         modes=PREFLIGHT_SMOKE, destructive=False, params={}),
    dict(name="Lakebase transaction", category=TestCategory.PROCESSING, kind=ExecutorKind.APP_INLINE,
         key="lakebase_smoke", tags=["lakebase", "postgres"], target_type="database",
         modes=ALL_MODES, destructive=False, params={}),
    dict(name="GitHub access", category=TestCategory.PROCESSING, kind=ExecutorKind.APP_INLINE,
         key="github_access", tags=["git", "github"], target_type="external_system",
         modes=PREFLIGHT_SMOKE, destructive=False, params={}),
    dict(name="External catalog validation", category=TestCategory.PROCESSING, kind=ExecutorKind.SQL_WAREHOUSE,
         key="external_catalog", tags=["uc", "catalog"], target_type="catalog",
         modes=PREFLIGHT_SMOKE, destructive=False, params={}),
    dict(name="Genie", category=TestCategory.PROCESSING, kind=ExecutorKind.APP_INLINE,
         key="genie_query", tags=["genie", "ai/bi"], target_type="genie_space",
         modes=[ExecutionMode.PREFLIGHT], destructive=False, params={}),
    dict(name="Agent (Enterprise only)", category=TestCategory.PROCESSING, kind=ExecutorKind.MODEL_SERVING,
         key="agent_enterprise", tags=["agent", "enterprise"], target_type="endpoint",
         modes=[ExecutionMode.PREFLIGHT], destructive=False, enterprise_only=True, params={}),

    # ---------------- Consumer / outbound ----------------
    dict(name="Power BI connectivity", category=TestCategory.CONSUMER, kind=ExecutorKind.REMOTE_AGENT,
         key="remote_agent", cap="powerbi_connectivity", tags=["consumer", "bi", "powerbi"], target_type="external_system",
         modes=[ExecutionMode.PREFLIGHT], destructive=False, params={"vantage_point": "bi-network"}),
    dict(name="Tableau connectivity", category=TestCategory.CONSUMER, kind=ExecutorKind.REMOTE_AGENT,
         key="remote_agent", cap="tableau_connectivity", tags=["consumer", "bi", "tableau"], target_type="external_system",
         modes=[ExecutionMode.PREFLIGHT], destructive=False, params={"vantage_point": "bi-network"}),
    dict(name="Salesforce outbound", category=TestCategory.CONSUMER, kind=ExecutorKind.APP_INLINE,
         key="salesforce_outbound", tags=["consumer", "salesforce", "http"], target_type="external_system",
         modes=PREFLIGHT_SMOKE, destructive=False, params={"connection_name": "salesforce_conn"}),
    dict(name="TM1 outbound", category=TestCategory.CONSUMER, kind=ExecutorKind.REMOTE_AGENT,
         key="remote_agent", cap="tm1_outbound", tags=["consumer", "tm1"], target_type="external_system",
         modes=[ExecutionMode.PREFLIGHT], destructive=False, params={"vantage_point": "finance-network"}),
    dict(name="Oracle outbound", category=TestCategory.CONSUMER, kind=ExecutorKind.REMOTE_AGENT,
         key="remote_agent", cap="oracle_outbound", tags=["consumer", "oracle"], target_type="external_system",
         modes=[ExecutionMode.PREFLIGHT], destructive=False, params={"vantage_point": "onprem-oracle"}),
    dict(name="N8N connectivity", category=TestCategory.CONSUMER, kind=ExecutorKind.APP_INLINE,
         key="n8n_connectivity", tags=["consumer", "n8n", "http"], target_type="external_system",
         modes=PREFLIGHT_SMOKE, destructive=False, params={"connection_name": "n8n_conn"}),
    dict(name="Other outbound integrations", category=TestCategory.CONSUMER, kind=ExecutorKind.APP_INLINE,
         key="outbound_generic", tags=["consumer", "outbound", "http"], target_type="external_system",
         modes=PREFLIGHT_SMOKE, destructive=False, params={"url": "https://example.com", "method": "GET"}),
]


def _make_definition(spec: dict) -> TestDefinition:
    return TestDefinition(
        display_name=spec["name"],
        description=f"{spec['category'].value} probe: {spec['name']}.",
        category=spec["category"],
        tags=spec["tags"],
        target_type=spec["target_type"],
        required_capabilities=[spec.get("cap", spec["key"])],
        supported_execution_modes=spec["modes"],
        executor_kind=spec["kind"],
        executor_key=spec["key"],
        auth_mode=AuthMode.APP,
        enterprise_only=spec.get("enterprise_only", False),
        destructive=spec.get("destructive", False),
        cleanup_policy=CleanupPolicy.ALWAYS if spec.get("destructive") else CleanupPolicy.NEVER,
        default_parameters=spec.get("params", {}),
        parameter_schema={"type": "object", "properties": {}},
        evidence_schema={"type": "object"},
        timeout_seconds=180 if spec["kind"] == ExecutorKind.DATABRICKS_JOB else 60,
    )


def seed_all(
    session: Session,
    with_sample_runs: bool = True,
    with_demo_targets: bool = True,
) -> None:
    """Seed the test catalog (idempotent).

    Always seeds the real test *definitions*, *suites*, and *schedules* - these
    are generic, environment-agnostic config the app needs to be useful.

    ``with_demo_targets``/``with_sample_runs`` add demo-only data (placeholder
    catalogs/workspaces and synthetic historical runs) for screenshots and local
    development. In a real workspace both are off, so probes run workspace-global
    against the app's own environment and the dashboards start empty.
    """
    existing = session.exec(select(TestDefinition)).first()
    if existing:
        logger.info("Seed skipped: definitions already present.")
        return

    logger.info("Seeding test catalog (%d definitions)...", len(CATALOG))
    definitions: list[TestDefinition] = []
    for spec in CATALOG:
        d = _make_definition(spec)
        session.add(d)
        definitions.append(d)
    session.commit()
    for d in definitions:
        session.refresh(d)

    # Suites by category + a full regression suite.
    by_cat: dict[TestCategory, list[TestDefinition]] = {}
    for d in definitions:
        by_cat.setdefault(d.category, []).append(d)

    suite_specs = [
        ("Producer Regression", "Inbound/producer connectivity + landing-zone writes", by_cat.get(TestCategory.PRODUCER, [])),
        ("Processing Regression", "In-workspace compute and services", by_cat.get(TestCategory.PROCESSING, [])),
        ("Consumer Regression", "Outbound/consumer connectivity", by_cat.get(TestCategory.CONSUMER, [])),
        ("Full Regression", "All probes across every category", definitions),
    ]
    for name, desc, members in suite_specs:
        suite = TestSuite(name=name, description=desc, tags=["seeded"])
        session.add(suite)
        session.commit()
        session.refresh(suite)
        for i, d in enumerate(members):
            session.add(SuiteMember(suite_id=suite.id, test_definition_id=d.id, sort_order=i))
    session.commit()

    # Demo targets: placeholder catalogs/workspaces for screenshots + local dev.
    # Off in a real workspace, where probes run workspace-global against the
    # app's own environment (the expand step uses a null target).
    targets: list[TestTarget] = []
    if with_demo_targets:
        targets = [
            TestTarget(name="sales_dev (temp schema)", target_type="catalog", catalog="sales_dev",
                       schema_name="temp", workspace_name="aws-prod-workspace", cloud="aws", region="us-east-1",
                       config={"catalog": "sales_dev", "schema": "temp"}),
            TestTarget(name="analytics_prod", target_type="catalog", catalog="analytics_prod",
                       workspace_name="aws-prod-workspace", cloud="aws", region="us-east-1",
                       config={"catalog": "analytics_prod"}),
            TestTarget(name="primary workspace", target_type="workspace",
                       workspace_name="aws-prod-workspace", cloud="aws", region="us-east-1", config={}),
        ]
        for t in targets:
            session.add(t)
        session.commit()

    # A nightly full-regression schedule (materialised as a Databricks Job in prod).
    full_suite = session.exec(select(TestSuite).where(TestSuite.name == "Full Regression")).first()
    if full_suite:
        session.add(Schedule(
            name="Nightly Full Regression", cron="0 0 7 * * ?", timezone="UTC",
            suite_ids=[full_suite.id], execution_mode=ExecutionMode.SMOKE, created_by="seed",
        ))
    session.add(Schedule(
        name="Hourly Connectivity Preflight", cron="0 0 * * * ?", timezone="UTC",
        execution_mode=ExecutionMode.PREFLIGHT, created_by="seed",
    ))
    session.commit()

    if with_sample_runs and targets:
        _seed_sample_runs(session, definitions, targets)


def _seed_sample_runs(session: Session, definitions: list[TestDefinition], targets: list[TestTarget]) -> None:
    """Synthesize historical runs across ~14 days for dashboard rendering."""
    logger.info("Seeding synthetic historical runs...")
    rng = random.Random(42)
    now = utcnow()
    fail_buckets = [
        RootCauseBucket.DNS_RESOLUTION_FAILURE,
        RootCauseBucket.NETWORK_POLICY_DENY,
        RootCauseBucket.OAUTH_SCOPE_MISSING,
        RootCauseBucket.PRIVATELINK_ENDPOINT_NOT_ESTABLISHED,
        RootCauseBucket.DATA_VALIDATION_FAILURE,
        RootCauseBucket.TLS_HANDSHAKE_FAILURE,
    ]

    for day in range(14):
        day_runs = rng.randint(6, 12)
        for _ in range(day_runs):
            d = rng.choice(definitions)
            t = rng.choice(targets)
            finished = now - timedelta(days=day, hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
            started = finished - timedelta(seconds=rng.randint(2, 40))
            roll = rng.random()
            if roll < 0.72:
                outcome, status, conn, bucket = ProbeOutcome.PASS, RunStatus.PASSED, ConnectivityOutcome.CONNECTABLE, None
            elif roll < 0.9:
                bucket = rng.choice(fail_buckets)
                connish = bucket in (RootCauseBucket.DNS_RESOLUTION_FAILURE, RootCauseBucket.NETWORK_POLICY_DENY,
                                     RootCauseBucket.PRIVATELINK_ENDPOINT_NOT_ESTABLISHED, RootCauseBucket.TLS_HANDSHAKE_FAILURE)
                outcome, status = ProbeOutcome.FAIL, RunStatus.FAILED
                conn = ConnectivityOutcome.NOT_CONNECTABLE if connish else ConnectivityOutcome.CONNECTABLE
            else:
                outcome, status, conn, bucket = ProbeOutcome.SKIP, RunStatus.PARTIAL, ConnectivityOutcome.UNKNOWN, None

            run = TestRun(
                test_definition_id=d.id, target_id=t.id, requested_by="seed",
                request_source=RequestSource.SCHEDULE, execution_mode=ExecutionMode.SMOKE,
                workspace_name=t.workspace_name, cloud="aws", region=t.region, catalog=t.catalog,
                git_sha="seed000", app_version="0.1.0",
                status=status, probe_outcome=outcome, connectivity_outcome=conn,
                started_at=started, finished_at=finished,
                duration_ms=int((finished - started).total_seconds() * 1000),
                summary_counts={outcome.value: 1},
                connectivity_summary={conn.value: 1},
                failure_buckets=[bucket.value] if bucket else [],
                root_cause_bucket=bucket,
                recommendation="See PrivateLink/NCC troubleshooting guide." if bucket else None,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            # A couple of representative steps per run.
            session.add(TestRunStep(run_id=run.run_id, step_kind=StepKind.AUTH, sort_order=0,
                                    outcome=ProbeOutcome.PASS, connectivity=ConnectivityOutcome.CONNECTABLE,
                                    message="Authenticated.", started_at=started, finished_at=finished))
            session.add(TestRunStep(
                run_id=run.run_id, step_kind=StepKind.FUNCTIONAL, sort_order=1,
                outcome=outcome, connectivity=conn,
                message="OK" if outcome == ProbeOutcome.PASS else (bucket.value if bucket else "skipped"),
                root_cause_bucket=bucket, started_at=started, finished_at=finished,
            ))
            session.commit()
    logger.info("Synthetic runs seeded.")
