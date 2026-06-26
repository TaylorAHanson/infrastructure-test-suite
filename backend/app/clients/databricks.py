"""Thin, lazy Databricks SDK wrappers bound via app resources.

Everything here is best-effort: in local/demo mode (no profile/binding) these
helpers report unavailability instead of raising, so the platform runs fully
without a workspace. No raw resource IDs or credentials live in this module -
they come from app resources / injected env.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.core.config import get_workspace_client, settings

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceContext:
    """Best-effort cloud/account/region/workspace context for a run."""

    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None
    cloud: str = "aws"
    account_id: Optional[str] = None
    region: Optional[str] = None
    available: bool = False


_cached_context: Optional[WorkspaceContext] = None


def databricks_available() -> bool:
    """True when a real Databricks identity is reachable."""
    if settings.is_databricks_app:
        return True
    try:
        get_workspace_client()
        return True
    except Exception:  # noqa: BLE001 - local without profile
        return False


def get_workspace_context() -> WorkspaceContext:
    global _cached_context
    if _cached_context is not None:
        return _cached_context
    ctx = WorkspaceContext()
    try:
        client = get_workspace_client()
        host = settings.workspace_host or client.config.host or ""
        ctx.workspace_name = host
        ctx.cloud = "aws"
        # Region/account are best-effort; not all are exposed via config.
        ctx.region = getattr(client.config, "azure_environment", None) or settings.ENVIRONMENT
        ctx.available = True
    except Exception as exc:  # noqa: BLE001
        logger.info("Databricks workspace context unavailable (demo mode): %s", exc)
        ctx.available = False
    _cached_context = ctx
    return ctx


def trigger_job(job_id: str, params: dict) -> Optional[str]:
    """Trigger a Databricks Job run; return the run id (or None in demo mode)."""
    try:
        client = get_workspace_client()
        run = client.jobs.run_now(job_id=int(job_id), job_parameters=params)
        return str(run.run_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not trigger Databricks job %s: %s", job_id, exc)
        return None


def upsert_cron_job(name: str, cron: str, timezone: str, params: dict, job_id: Optional[str]) -> Optional[str]:
    """Create or update a Databricks Job that fires on ``cron``.

    The job runs the bundled suite-runner task; each fire posts a run_request
    back to the control plane. Returns the job id, or None in demo mode.
    """
    try:
        from databricks.sdk.service import jobs as j

        client = get_workspace_client()
        schedule = j.CronSchedule(
            quartz_cron_expression=cron,
            timezone_id=timezone,
            pause_status=j.PauseStatus.UNPAUSED,
        )
        if job_id:
            existing = client.jobs.get(job_id=int(job_id))
            new_settings = existing.settings
            new_settings.schedule = schedule
            client.jobs.reset(job_id=int(job_id), new_settings=new_settings)
            return str(job_id)
        # Creating a job from the app is optional; the bundle typically owns the
        # canonical job. We log intent and return None unless a runner exists.
        logger.info("Schedule '%s' (cron=%s) should be materialised by the bundle job.", name, cron)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not upsert cron job '%s': %s", name, exc)
        return None
