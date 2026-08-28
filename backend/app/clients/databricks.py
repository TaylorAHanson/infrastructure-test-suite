"""Thin, lazy Databricks SDK wrappers bound via app resources.

Everything here is best-effort: in local/demo mode (no profile/binding) these
helpers report unavailability instead of raising, so the platform runs fully
without a workspace. No raw resource IDs or credentials live in this module -
they come from app resources / injected env.

**Per-target identity (ADR-0003/0018).** Each Target authenticates as its own
OAuth **M2M service principal**, referenced by a Databricks secret-scope
``scope/key`` **identity handle** — the platform never stores the secret value.
Tokens are minted **on demand**, cached **in-memory only** (never persisted),
and short-lived; the SP secret rotates in-scope out-of-band. A Run whose Test
sets a ``run-as-role`` adds ``assume_group=<group-id>`` to the token request so
the role's grants *replace* the SP's for the token's life (native RBAC, no new
credentials, CI-safe). :func:`probe_identity` is the test-connection-before-save
check (ADR-0003 §3): a broken credential is caught at configuration time.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

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


# ---------------------------------------------------------------------------
# Per-target OAuth M2M identity (ADR-0003) + assume-role (ADR-0018)
# ---------------------------------------------------------------------------
#: Default OAuth scope for a workspace-level M2M token request.
_DEFAULT_SCOPE = "all-apis"
#: Convention: the client id lives beside the secret under ``{key}_client_id``.
_CLIENT_ID_SUFFIX = "_client_id"


class IdentityError(ValueError):
    """Raised when a target's identity handle is missing or malformed."""


@dataclass(frozen=True)
class SpCredentials:
    """An OAuth M2M service principal's client id + secret (never persisted)."""

    client_id: str
    client_secret: str


@dataclass
class TargetToken:
    """A short-lived token for ``(target-identity, assumed-role?)``.

    ``assumed_group`` is the ``assume_group`` value used (None = the SP itself).
    Held in memory only, keyed by ``(identity_handle, assumed_group)``.
    """

    access_token: str
    identity_handle: str
    assumed_group: Optional[str]
    expires_at: float


@dataclass
class ProbeResult:
    """Outcome of a test-connection probe (ADR-0003 §3)."""

    ok: bool
    message: str


# In-memory only. No token is ever written to disk or the DB (ADR-0003 §4).
_token_cache: dict[tuple[str, Optional[str]], TargetToken] = {}
# A small safety margin so a token near expiry is re-minted, not handed out.
_TOKEN_SKEW_SECONDS = 60

SecretReader = Callable[[str, str], str]
TokenPoster = Callable[[str, dict, tuple[str, str]], dict]


def parse_identity_handle(handle: Optional[str]) -> tuple[str, str]:
    """Split a ``scope/key`` identity handle, validating its shape.

    Raises :class:`IdentityError` when missing or not exactly ``scope/key`` —
    the deterministic failure the test-connection guard reports at save time.
    """
    if not handle or not handle.strip():
        raise IdentityError("identity handle is required (a Databricks secret-scope 'scope/key')")
    parts = handle.split("/")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise IdentityError(f"identity handle must be 'scope/key', got '{handle}'")
    return parts[0].strip(), parts[1].strip()


def _default_secret_reader(scope: str, key: str) -> str:
    """Read a secret value from a Databricks secret scope (real mode only)."""
    client = get_workspace_client()
    import base64

    resp = client.secrets.get_secret(scope=scope, key=key)
    raw = resp.value or ""
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:  # noqa: BLE001 - already-plain values
        return raw


def read_sp_credentials(handle: str, *, secret_reader: Optional[SecretReader] = None) -> SpCredentials:
    """Resolve a target's SP client id + secret from its ``scope/key`` handle.

    Convention: the client **secret** lives at ``scope/key`` and the client
    **id** at ``scope/key_client_id`` (a sibling secret). The platform reads
    both fresh on demand — it stores neither.
    """
    scope, key = parse_identity_handle(handle)
    reader = secret_reader or _default_secret_reader
    client_secret = reader(scope, key)
    client_id = reader(scope, f"{key}{_CLIENT_ID_SUFFIX}")
    return SpCredentials(client_id=client_id, client_secret=client_secret)


def build_token_request(client_id: str, assume_group: Optional[str] = None) -> dict:
    """Build the ``client_credentials`` token-request form body.

    The **assume-role branch** (ADR-0018): when ``assume_group`` is set, the
    role's group id is added to the workspace-level request so the role's grants
    replace the SP's for the token's 1 h life. The SP path omits it entirely.
    """
    data = {
        "grant_type": "client_credentials",
        "scope": _DEFAULT_SCOPE,
        "client_id": client_id,
    }
    if assume_group:
        data["assume_group"] = assume_group
    return data


def acquire_target_token(
    identity_handle: str,
    host: Optional[str],
    assume_group: Optional[str] = None,
    *,
    secret_reader: Optional[SecretReader] = None,
    token_poster: Optional[TokenPoster] = None,
    ttl_seconds: int = 3600,
) -> TargetToken:
    """Mint (or return a cached) short-lived token for a target identity.

    Cached in memory keyed by ``(identity_handle, assume_group)`` so the SP and
    each assumed role get distinct tokens; nothing is persisted. Re-mints when
    the cached token is within :data:`_TOKEN_SKEW_SECONDS` of expiry.
    """
    cache_key = (identity_handle, assume_group)
    cached = _token_cache.get(cache_key)
    if cached and cached.expires_at - _TOKEN_SKEW_SECONDS > time.time():
        return cached

    creds = read_sp_credentials(identity_handle, secret_reader=secret_reader)
    data = build_token_request(creds.client_id, assume_group)
    poster = token_poster or _default_token_poster
    token_url = f"{(host or settings.workspace_host or '').rstrip('/')}/oidc/v1/token"
    resp = poster(token_url, data, (creds.client_id, creds.client_secret))
    token = TargetToken(
        access_token=str(resp.get("access_token", "")),
        identity_handle=identity_handle,
        assumed_group=assume_group,
        expires_at=time.time() + int(resp.get("expires_in", ttl_seconds)),
    )
    _token_cache[cache_key] = token
    return token


def _default_token_poster(url: str, data: dict, auth: tuple[str, str]) -> dict:
    """POST an OAuth token request to the workspace OIDC endpoint (real mode)."""
    import httpx

    resp = httpx.post(url, data=data, auth=auth, timeout=30)
    resp.raise_for_status()
    return resp.json()


def clear_token_cache() -> None:
    """Drop all in-memory tokens (used on rotation / in tests)."""
    _token_cache.clear()


def probe_identity(
    identity_handle: Optional[str],
    host: Optional[str] = None,
    assume_group: Optional[str] = None,
    *,
    secret_reader: Optional[SecretReader] = None,
    token_poster: Optional[TokenPoster] = None,
) -> ProbeResult:
    """Test-connection-before-save (ADR-0003 §3): prove the identity is usable.

    A malformed or missing handle fails deterministically (blocking the save).
    With live credentials injected (or a real workspace binding) it mints a
    token to prove reachability. In demo/local mode with no binding a
    well-formed handle is accepted with a note — the platform stays fully
    exercisable without a workspace, and the format guard still catches typos.
    """
    try:
        parse_identity_handle(identity_handle)
    except IdentityError as exc:
        return ProbeResult(ok=False, message=str(exc))

    # If we can actually mint a token (real binding or injected stubs), do so —
    # that is the real proof of a working credential.
    if token_poster is not None or secret_reader is not None or databricks_available():
        try:
            token = acquire_target_token(
                identity_handle, host, assume_group,
                secret_reader=secret_reader, token_poster=token_poster)
            if not token.access_token:
                return ProbeResult(ok=False, message="token endpoint returned no access_token")
            suffix = f" (assumed role {assume_group})" if assume_group else ""
            return ProbeResult(ok=True, message=f"Connection verified{suffix}.")
        except Exception as exc:  # noqa: BLE001 - any failure blocks the save
            return ProbeResult(ok=False, message=f"connection failed: {exc}")

    return ProbeResult(ok=True, message="Handle well-formed; verified in demo mode (no live workspace).")


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
