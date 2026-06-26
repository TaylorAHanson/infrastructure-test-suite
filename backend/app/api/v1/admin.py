"""Admin / settings endpoints: resource bindings, policies, feature flags."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.clients.databricks import databricks_available, get_workspace_context
from app.core.config import settings
from app.db.session import get_session
from app.executors.registry import registry

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
    }
