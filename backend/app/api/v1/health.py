"""Health and version endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.clients.databricks import databricks_available, get_workspace_context
from app.core.config import settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": settings.APP_VERSION, "environment": settings.ENVIRONMENT}


@router.get("/info")
def info() -> dict:
    ctx = get_workspace_context()
    return {
        "project": settings.PROJECT_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "git_sha": settings.GIT_SHA,
        "is_databricks_app": settings.is_databricks_app,
        "databricks_available": databricks_available(),
        "using_postgres": settings.using_postgres,
        "has_lakebase": settings.has_lakebase,
        "workspace": {
            "name": ctx.workspace_name,
            "cloud": ctx.cloud,
            "region": ctx.region,
            "available": ctx.available,
        },
        "demo_mode": not databricks_available(),
    }
