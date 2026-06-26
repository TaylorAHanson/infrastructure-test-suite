"""Admin / settings endpoints: resource bindings, policies, feature flags."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.clients.databricks import databricks_available, get_workspace_context
from app.core.config import settings
from app.db.session import get_session
from app.executors.registry import registry
from app.models import (
    RunRequest,
    Schedule,
    SuiteMember,
    TestDefinition,
    TestRun,
    TestRunStep,
    TestSuite,
    TestTarget,
)

router = APIRouter()


@router.get("/settings")
def get_admin_settings(session: Session = Depends(get_session)) -> dict:
    ctx = get_workspace_context()
    return {
        "resource_bindings": {
            "lakebase": {"bound": settings.has_lakebase, "host": settings.PGHOST},
            "sql_warehouse": {"bound": bool(settings.DATABRICKS_WAREHOUSE_ID),
                              "warehouse_id": settings.DATABRICKS_WAREHOUSE_ID},
            "model_serving": {"bound": bool(settings.MODEL_SERVING_ENDPOINT),
                              "endpoint": settings.MODEL_SERVING_ENDPOINT},
            "suite_runner_job": {"bound": bool(settings.SUITE_RUNNER_JOB_ID),
                                 "job_id": settings.SUITE_RUNNER_JOB_ID},
            "remote_agent_signing": {"configured": bool(settings.REMOTE_AGENT_SIGNING_SECRET)},
        },
        "concurrency_policy": {
            "poller_enabled": settings.POLLER_ENABLED,
            "interval_seconds": settings.POLLER_INTERVAL_SECONDS,
            "batch_size": settings.POLLER_BATCH_SIZE,
            "max_concurrent": settings.POLLER_MAX_CONCURRENT,
            "lock_timeout_minutes": settings.POLLER_LOCK_TIMEOUT_MINUTES,
        },
        "telemetry": {
            "otel_enabled": settings.OTEL_ENABLED,
            "otel_endpoint": settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        },
        "feature_flags": {
            "seed_on_startup": settings.SEED_ON_STARTUP,
            "is_databricks_app": settings.is_databricks_app,
            "demo_mode": not databricks_available(),
        },
        "registered_executor_keys": sorted(registry.keys()),
        "workspace": {"name": ctx.workspace_name, "cloud": ctx.cloud, "region": ctx.region},
        "environment": settings.ENVIRONMENT,
        "version": settings.APP_VERSION,
        "catalog_counts": {
            "test_definitions": len(session.exec(select(TestDefinition)).all()),
            "suites": len(session.exec(select(TestSuite)).all()),
            "targets": len(session.exec(select(TestTarget)).all()),
            "schedules": len(session.exec(select(Schedule)).all()),
            "runs": len(session.exec(select(TestRun)).all()),
        },
    }


@router.post("/seed")
def seed_catalog(demo: bool = False, session: Session = Depends(get_session)) -> dict:
    """Seed the real test catalog on demand (idempotent).

    Pass ``?demo=true`` to also add placeholder targets + synthetic historical
    runs (for screenshots/demos). Safe to call repeatedly - it no-ops once
    definitions already exist.
    """
    from app.services.seed import seed_all

    seed_all(session, with_sample_runs=demo, with_demo_targets=demo)
    return {
        "seeded": True,
        "demo": demo,
        "test_definitions": len(session.exec(select(TestDefinition)).all()),
        "suites": len(session.exec(select(TestSuite)).all()),
        "targets": len(session.exec(select(TestTarget)).all()),
    }


def _purge_models(session: Session, models) -> dict[str, int]:
    """Delete all rows for each model in FK-dependency order.

    We flush after each model so child rows are gone before their parents -
    Postgres enforces FK constraints and the ORM unit-of-work doesn't infer the
    order on its own (no declared relationships between these tables).
    """
    counts: dict[str, int] = {}
    for model in models:
        rows = session.exec(select(model)).all()
        counts[model.__name__] = len(rows)
        for row in rows:
            session.delete(row)
        session.flush()
    session.commit()
    return counts


@router.post("/purge-runs")
def purge_runs(session: Session = Depends(get_session)) -> dict:
    """Delete all run history (steps, runs, requests) - keeps the catalog."""
    counts = _purge_models(session, (TestRunStep, TestRun, RunRequest))
    return {"purged": True, "counts": counts}


@router.post("/purge-all")
def purge_all(session: Session = Depends(get_session)) -> dict:
    """Delete the entire catalog AND run history (full reset)."""
    counts = _purge_models(
        session,
        (TestRunStep, TestRun, RunRequest, SuiteMember, Schedule, TestSuite, TestTarget, TestDefinition),
    )
    return {"purged": True, "counts": counts}
