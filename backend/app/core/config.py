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
    APP_VERSION: str = "1.1.0"
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
    # Postgres schema the app owns and creates its tables/enums in. We never use
    # `public`: PG 15+ revokes CREATE on it for non-owner roles, so create_all()
    # (and Lakebase's app service principal) would hit "permission denied for
    # schema public". The app role can CREATE its own schema via the postgres
    # resource's CAN_CONNECT_AND_CREATE grant.
    DB_SCHEMA: str = "regression"

    # ----- Databricks resource bindings (supplied by app resources) -----
    DATABRICKS_PROFILE: str = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
    DATABRICKS_HOST: Optional[str] = None
    MODEL_SERVING_ENDPOINT: Optional[str] = None

    # ----- Poller (async run processor) ---------------------------------
    POLLER_ENABLED: bool = True
    POLLER_INTERVAL_SECONDS: int = 5
    POLLER_BATCH_SIZE: int = 25
    POLLER_MAX_CONCURRENT: int = 10
    POLLER_LOCK_TIMEOUT_MINUTES: int = 15
    # The nightly full run of every active Test against every Target, which the
    # poller starts (app.services.nightly). Five-field cron in UTC; empty turns
    # it off, as does a schedule status of PAUSED.
    NIGHTLY_CRON: str = ""
    NIGHTLY_SCHEDULE_STATUS: str = "UNPAUSED"

    # ----- Run engine (ADR-0005/0007/0011) ------------------------------
    # The single configurable concurrency dial (ADR-0007 §5): the max number of
    # CheckRuns executing at once across the whole process (every Run shares
    # one pool). Bounds thundering-herd against ~100 targets. A latency/scope
    # control, never a cost control (ADR-0007 §6).
    MAX_RUN_CONCURRENCY: int = 10
    # Requests are orchestrated on a small dispatcher pool so a long Run never
    # occupies the API threadpool or the poller tick; its threads mostly wait on
    # the CheckRun pool above. Inline dispatch (on the caller's thread) is for
    # tests, which read a run's result right after POST /runs.
    RUN_DISPATCH_WORKERS: int = 16
    RUN_DISPATCH_INLINE: bool = False
    # Per-target lease (ADR-0011). The holder renews the lease by heartbeat while
    # alive; a lease whose heartbeat lapses past the TTL is reclaimable, so a
    # crashed holder can't deadlock a target. Heartbeat interval must be well
    # under the TTL.
    LEASE_TTL_SECONDS: int = 90
    LEASE_HEARTBEAT_SECONDS: int = 20
    # Server-side max run duration (ADR-0011 §5) — distinct from the caller-owned
    # poll timeout (ADR-0005 §2). A run holding a lease past this deadline is
    # force-reclaimed even if still heartbeating, so a wedged run frees its lock.
    MAX_RUN_DURATION_SECONDS: int = 1800
    # Python-script Tests: wall-clock limit and address-space cap per script run.
    # Long enough for a classic cluster launch, inside MAX_RUN_DURATION_SECONDS.
    SCRIPT_TIMEOUT_SECONDS: int = 1500
    SCRIPT_MEMORY_MB: int = 1024
    # Bounds on one brokered SDK call (per HTTP attempt, and across the SDK's
    # retries). The script timer only stops the sandbox; these stop a hung call
    # from holding the run, and its target lease, past it. Scripts that wait on
    # long jobs poll, so each call stays short.
    SCRIPT_SDK_HTTP_TIMEOUT_SECONDS: int = 60
    SCRIPT_SDK_RETRY_TIMEOUT_SECONDS: int = 120
    # Output budget for the editor agent's replies (full scripts need room).
    ASSISTANT_MAX_TOKENS: int = 6000

    # ----- Retained history (ADR-0015) ----------------------------------
    # The single configurable retention knob: check-result history is pruned to
    # this rolling window. Cost is a non-goal (ADR-0007) — one generous default,
    # not a tuned budget. The most-recent green per (target, check) is preserved
    # past the window regardless, so long-stable-check provenance stays honest.
    RETENTION_WINDOW_DAYS: int = 90
    # How often the poller prunes history (its other duties run every tick).
    RETENTION_SWEEP_INTERVAL_SECONDS: int = 3600

    # ----- Findings feed (ADR-0009, #33) --------------------------------
    # The Recent-deployments lane is a rolling window of CI-triggered,
    # commit-scoped runs; a failing series older than this (or not
    # deploy-attributable) defaults to the Overnight lane. One generous knob,
    # not a tuned budget — cost stays a non-goal (ADR-0007).
    FINDINGS_RECENT_WINDOW_HOURS: int = 24

    # ----- Flaky detection (ADR-0008, #34/#11) --------------------------
    # A check whose recent DETERMINISTIC verdicts alternate between green and
    # bad is flagged flaky — advisory only, never a gating regression. This
    # bounds "recent": the scan considers the last N deterministic terminal
    # verdicts per (target, check). One generous knob; cost is a non-goal.
    FLAKY_WINDOW_RUNS: int = 10

    # ----- Run-completion webhook (ADR-0016) ----------------------------
    # The platform's only push: a single generic outbound POST on run
    # completion. The sink URL(s) + firing toggle live in a Lakebase config
    # record (:class:`app.models.WebhookConfig`), not here — these are only the
    # delivery knobs. Best-effort: a bounded retry, failures logged, no durable
    # queue (ADR-0016 §5).
    #
    # Base URL of this deployment (its public app URL), used to build the deep
    # link into the "What's Interesting" view carried in the payload. Empty
    # yields a relative link the receiver can resolve against its known host.
    WEBHOOK_DEEP_LINK_BASE_URL: str = ""
    WEBHOOK_MAX_RETRIES: int = 3
    WEBHOOK_RETRY_BACKOFF_SECONDS: float = 0.5
    WEBHOOK_TIMEOUT_SECONDS: float = 10.0
    # Deliver on a background thread so the run engine never blocks on the POST.
    # Tests flip this off for deterministic, inline delivery.
    WEBHOOK_DELIVERY_ASYNC: bool = True

    # ----- Telemetry ----------------------------------------------------
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None

    # ----- Behaviour flags ----------------------------------------------
    # Auto-seeding is OFF everywhere by default: the platform creates no
    # mock/demo data. Real targets/tests are added via the UI/API. The built-in
    # test-definition catalog can still be seeded on demand via POST /admin/seed.
    SEED_ON_STARTUP: bool = False
    # Demo-only placeholder targets (no synthetic run history — there is no
    # such seeding path). Never enabled implicitly; opt in explicitly for
    # screenshots/local dev only.
    SEED_DEMO_DATA: bool = False

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
def get_workspace_client(**config):
    """Return an authenticated ``WorkspaceClient``.

    Uses injected service-principal OAuth credentials inside a Databricks App;
    falls back to the local CLI profile otherwise. Raises if the SDK cannot
    authenticate so callers can degrade to demo/mock behaviour. ``config`` is
    passed to the SDK ``Config`` (e.g. ``http_timeout_seconds``).
    """
    from databricks.sdk import WorkspaceClient

    if config:
        from databricks.sdk.config import Config

        if not settings.is_databricks_app:
            config.setdefault("profile", settings.DATABRICKS_PROFILE)
        return WorkspaceClient(config=Config(**config))
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
