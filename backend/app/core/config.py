"""Application configuration and dual-mode Databricks authentication.

Two runtime modes are supported:

* **Databricks App** (``DATABRICKS_APP_NAME`` is set): the platform injects a
  dedicated service-principal OAuth identity and Lakebase ``PG*`` env vars. We
  use ``WorkspaceClient()`` with no arguments and let the SDK pick up the
  injected credentials. **No PATs** are used for app-to-Databricks auth.
* **Local development**: we fall back to SQLite and the developer's Databricks
  CLI profile (``DATABRICKS_PROFILE``) so the app runs end-to-end without a
  workspace binding.

Nothing here hardcodes resource IDs or secrets - everything is read from the
environment that Databricks App resources populate, or from a local ``.env``.
"""
from __future__ import annotations

import functools
import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- App metadata -------------------------------------------------
    PROJECT_NAME: str = "databricks-regression-validation"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "local"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    GIT_SHA: str = os.environ.get("GIT_SHA", "unknown")

    # ----- Networking ---------------------------------------------------
    # Databricks Apps inject the port to bind. Never hardcode it elsewhere.
    APP_PORT: int = int(os.environ.get("DATABRICKS_APP_PORT", os.environ.get("PORT", "8000")))
    CORS_ORIGINS: str = "*"

    # ----- Database -----------------------------------------------------
    # If DATABASE_URL is set, it wins. Otherwise we derive: Lakebase Postgres
    # when PGHOST is present (Databricks App), else a local SQLite file.
    DATABASE_URL: Optional[str] = None
    PGHOST: Optional[str] = None
    PGPORT: str = "5432"
    PGDATABASE: Optional[str] = None
    PGUSER: Optional[str] = None
    PGSSLMODE: str = "require"
    SQLITE_PATH: str = "regression_platform.db"

    # ----- Databricks resource bindings (supplied by app resources) -----
    DATABRICKS_PROFILE: str = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
    DATABRICKS_HOST: Optional[str] = None
    DATABRICKS_WAREHOUSE_ID: Optional[str] = None
    MODEL_SERVING_ENDPOINT: Optional[str] = None
    # Job that fans out heavy/scheduled suite execution. Created/updated by the
    # bundle; the app only needs the id to trigger it.
    SUITE_RUNNER_JOB_ID: Optional[str] = None

    # ----- Poller (async run processor) ---------------------------------
    POLLER_ENABLED: bool = True
    POLLER_INTERVAL_SECONDS: int = 5
    POLLER_BATCH_SIZE: int = 25
    POLLER_MAX_CONCURRENT: int = 10
    POLLER_LOCK_TIMEOUT_MINUTES: int = 15

    # ----- Telemetry ----------------------------------------------------
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None

    # ----- Remote agents ------------------------------------------------
    # HMAC shared secret used to verify signed results posted by remote agents.
    # In Databricks this should come from a secret resource, never source code.
    REMOTE_AGENT_SIGNING_SECRET: Optional[str] = None

    # ----- Behaviour flags ----------------------------------------------
    SEED_ON_STARTUP: bool = True

    # ---------------- Derived helpers ----------------------------------
    @property
    def is_databricks_app(self) -> bool:
        return bool(os.environ.get("DATABRICKS_APP_NAME"))

    @property
    def has_lakebase(self) -> bool:
        return bool(self.PGHOST)

    @property
    def workspace_host(self) -> Optional[str]:
        """Workspace host URL with an https:// scheme guaranteed.

        In Databricks Apps ``DATABRICKS_HOST`` is just a hostname (no scheme),
        which causes 401s against serving endpoints if used verbatim.
        """
        host = self.DATABRICKS_HOST
        if host and not host.startswith("http"):
            return f"https://{host}"
        return host

    def sqlalchemy_url(self) -> str:
        """Build the SQLAlchemy URL for the active runtime.

        Lakebase auth uses a short-lived OAuth token as the password; it is not
        embedded here. The DB layer injects a fresh token per connection via a
        SQLAlchemy ``do_connect`` event (see :mod:`app.db.session`).
        """
        if self.DATABASE_URL:
            return self.DATABASE_URL
        if self.has_lakebase:
            # Password intentionally omitted - injected at connect time.
            return (
                f"postgresql+psycopg2://{self.PGUSER}@{self.PGHOST}:{self.PGPORT}"
                f"/{self.PGDATABASE}?sslmode={self.PGSSLMODE}"
            )
        return f"sqlite:///{self.SQLITE_PATH}"

    @property
    def using_postgres(self) -> bool:
        return self.sqlalchemy_url().startswith("postgresql")


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# --------------------------------------------------------------------------
# Databricks SDK helpers (lazy - importing databricks-sdk is optional locally)
# --------------------------------------------------------------------------
def get_workspace_client():
    """Return an authenticated ``WorkspaceClient``.

    Uses injected service-principal OAuth credentials inside a Databricks App;
    falls back to the local CLI profile otherwise. Raises if the SDK cannot
    authenticate so callers can degrade to demo/mock behaviour.
    """
    from databricks.sdk import WorkspaceClient

    if settings.is_databricks_app:
        return WorkspaceClient()
    return WorkspaceClient(profile=settings.DATABRICKS_PROFILE)


def get_databricks_oauth_token() -> str:
    """Return a bearer token for Lakebase / serving-endpoint auth.

    Works for both M2M (app SP) and U2M (local profile). Never returns a PAT
    configured in source - it asks the SDK to authenticate the active config.
    """
    client = get_workspace_client()
    headers = client.config.authenticate()
    auth = headers.get("Authorization", "") if headers else ""
    return auth.replace("Bearer ", "").strip()
