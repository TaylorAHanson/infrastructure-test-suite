"""The run-completion webhook — the platform's only push (ADR-0016).

A **single generic outbound HTTP POST** to a configured sink fires when a
completed run produces **≥ 1 finding** — silent on all-green, with a toggle to
widen to every completion. It exists for the **overnight-drift gap**: a nightly
regression no one is polling for (CI polls the Run API; the SSC reads pass/fail;
humans read the view — none of them need a push). Pointing the sink at
Slack / PagerDuty / email is the adopter's receiver, not this platform's concern.

The pieces here are deliberately small and separately testable:

* :func:`get_config` / :func:`update_config` — the single global sink config
  (:class:`WebhookConfig`), editable by any human and **audited** as a
  config-plane mutation (the sink is itself config, ADR-0014).
* :func:`run_findings` — the run's findings, reusing #10's actionability
  (:data:`app.services.findings.ACTIONABLE_VERDICTS`) so "≥ 1 finding" means
  exactly what the "What's Interesting" view surfaces — no new regression logic.
* :func:`build_payload` — verdict + ``bucket @ stage`` summary + commit/event
  ref + a deep link into the view (ADR-0016 §4).
* :func:`deliver` — **best-effort** POST with a bounded retry; a failure is
  **logged, never fatal**; there is no durable queue (ADR-0016 §5).
* :func:`emit_run_completion` — the emitter the run engine calls on a terminal
  run; decides fire/silent, builds the payload, and dispatches delivery.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.enums import AuditAction, TERMINAL_STATUSES
from app.core.ids import utcnow
from app.core.taxonomy import bucket_at_stage
from app.models import Check, CheckRun, RunRequest, WebhookConfig, WEBHOOK_CONFIG_ID
from app.services import findings
from app.services.audit import record_audit

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Sink config — one global Lakebase record, editable + audited
# --------------------------------------------------------------------------
def get_config(session: Session) -> WebhookConfig:
    """The single global webhook-sink config, created (off) on first read.

    The default install ships the webhook **off** (no sink, ``enabled=False``),
    so an un-configured platform pushes nothing.
    """
    config = session.get(WebhookConfig, WEBHOOK_CONFIG_ID)
    if config is None:
        config = WebhookConfig(id=WEBHOOK_CONFIG_ID)
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def update_config(
    session: Session,
    *,
    actor: str,
    actor_kind: str = "user",
    urls: Optional[list[str]] = None,
    enabled: Optional[bool] = None,
    fire_on_all_completions: Optional[bool] = None,
    request_source: str = "API",
) -> WebhookConfig:
    """Edit the sink config and **audit** the change (ADR-0016 §2, ADR-0014).

    Any authenticated human may edit it (ADR-0010) with **no ceremony** — the
    sink can't defeat a safety gate — but the edit is recorded as a config-plane
    mutation, before→after, so changing where the platform pushes is traceable.
    Only the fields passed are changed; the rest are left as they stand.
    """
    config = get_config(session)
    before = {"urls": list(config.urls), "enabled": config.enabled,
              "fire_on_all_completions": config.fire_on_all_completions}

    if urls is not None:
        # Normalise: trim blanks and drop empties so a stray "" never becomes a
        # POST target. Order is preserved.
        config.urls = [u.strip() for u in urls if u and u.strip()]
    if enabled is not None:
        config.enabled = enabled
    if fire_on_all_completions is not None:
        config.fire_on_all_completions = fire_on_all_completions
    config.updated_by = actor
    config.updated_at = utcnow()
    session.add(config)

    after = {"urls": list(config.urls), "enabled": config.enabled,
             "fire_on_all_completions": config.fire_on_all_completions}
    record_audit(
        session, actor=actor, actor_kind=actor_kind,
        action=AuditAction.WEBHOOK_CONFIG_CHANGE.value,
        entity_type="webhook_config", entity_id=config.id,
        summary=f"Webhook sink updated ({len(config.urls)} url(s), "
                f"enabled={config.enabled}, all-completions={config.fire_on_all_completions}).",
        before=before, after=after, request_source=request_source, commit=False,
    )
    session.commit()
    session.refresh(config)
    return config


# --------------------------------------------------------------------------
# Findings for a run + payload
# --------------------------------------------------------------------------
def run_findings(session: Session, request_id: str) -> list[CheckRun]:
    """The run's findings — its actionable CheckRuns (FAIL/ERROR/BLOCKED_BY_SAFETY).

    Reuses #10's actionability verbatim (:data:`findings.ACTIONABLE_VERDICTS`),
    so a run is "all-green" exactly when it has none — including a failing
    **advisory** check, which the view surfaces as a finding too.
    """
    runs = session.exec(
        select(CheckRun).where(CheckRun.request_id == request_id)
    ).all()
    return [r for r in runs if r.verdict in findings.ACTIONABLE_VERDICTS]


def _deep_link(run_id: str) -> str:
    """A link into the "What's Interesting" view scoped to this run (ADR-0016 §4).

    Uses the configured deployment base URL; empty base yields a relative link
    the receiver resolves against its own known host.
    """
    base = (get_settings().WEBHOOK_DEEP_LINK_BASE_URL or "").rstrip("/")
    return f"{base}/analytics?run_id={run_id}"


def build_payload(session: Session, request: RunRequest,
                  fired: list[CheckRun]) -> dict:
    """The completion POST body (ADR-0016 §4).

    Carries the run verdict, the run's finding summary as ``bucket @ stage``, the
    commit / non-deploy event ref, the target(s), and a deep link — enough to
    triage from the notification and click through to the view.
    """
    check_names = {c.id: c.display_name for c in session.exec(select(Check)).all()}
    findings_out = [
        {
            "run_id": r.run_id,
            "check_id": r.check_id,
            "check_name": check_names.get(r.check_id, r.check_id),
            "target_id": r.target_id,
            "workspace_name": r.workspace_name,
            "verdict": r.verdict.value,
            "gating": r.gating,
            "bucket_at_stage": bucket_at_stage(r.failure_bucket, r.failure_stage),
        }
        for r in fired
    ]
    # A compact one-line human summary for receivers that only render text.
    if findings_out:
        summary = f"{len(findings_out)} finding(s): " + ", ".join(
            f"{f['bucket_at_stage']}"
            + (f" ({f['workspace_name']})" if f["workspace_name"] else "")
            for f in findings_out
        )
    else:
        summary = "run completed all-green (fired on the all-completions toggle)."

    # Distinct targets touched by the run, in first-seen order.
    targets: list[dict] = []
    seen: set = set()
    for r in session.exec(
        select(CheckRun).where(CheckRun.request_id == request.id)
    ).all():
        key = r.target_id
        if key in seen:
            continue
        seen.add(key)
        targets.append({"target_id": r.target_id, "workspace_name": r.workspace_name})

    return {
        "event": "run.completed",
        "run_id": request.id,
        "verdict": request.status.value,
        "phase": "terminal",
        "commit_ref": request.git_sha,
        "event_ref": request.event_ref,
        "targets": targets,
        "finding_count": len(findings_out),
        "findings": findings_out,
        "summary": summary,
        "deep_link": _deep_link(request.id),
        "generated_at": utcnow().isoformat(),
    }


# --------------------------------------------------------------------------
# Best-effort delivery (bounded retry, failures logged, no durable queue)
# --------------------------------------------------------------------------
def _post_json(url: str, payload: dict, timeout: float) -> None:
    """Perform one outbound POST, raising on a network error or non-2xx.

    Isolated so tests can substitute a captured/stubbed sink for the real HTTP.
    """
    import httpx

    resp = httpx.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()


def _deliver_one(url: str, payload: dict) -> dict:
    """Deliver to one sink with a bounded retry; never raises.

    A failure is **logged, not fatal** (ADR-0016 §5): the webhook is a nudge, the
    view + Lakebase stay the source of truth, so at-least-once best-effort is
    deliberately sufficient. Returns a small result dict for observability/tests.
    """
    settings = get_settings()
    attempts = max(1, settings.WEBHOOK_MAX_RETRIES)
    last_error: Optional[str] = None
    for attempt in range(1, attempts + 1):
        try:
            _post_json(url, payload, settings.WEBHOOK_TIMEOUT_SECONDS)
            return {"url": url, "ok": True, "attempts": attempt}
        except Exception as exc:  # noqa: BLE001 - delivery is best-effort
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Webhook POST to %s failed (attempt %d/%d): %s",
                           url, attempt, attempts, last_error)
            if attempt < attempts:
                time.sleep(settings.WEBHOOK_RETRY_BACKOFF_SECONDS * attempt)
    logger.error("Webhook delivery to %s gave up after %d attempt(s): %s",
                 url, attempts, last_error)
    return {"url": url, "ok": False, "attempts": attempts, "error": last_error}


def deliver(urls: list[str], payload: dict) -> list[dict]:
    """Deliver the payload to every configured sink, best-effort. Never raises."""
    return [_deliver_one(url, payload) for url in urls]


# --------------------------------------------------------------------------
# The emitter the run engine calls on a terminal run
# --------------------------------------------------------------------------
def emit_run_completion(request_id: str) -> Optional[dict]:
    """Fire the run-completion webhook for a terminal run (ADR-0016). Never raises.

    Silent unless the sink is enabled **and** the run produced ≥ 1 finding
    (unless the all-completions toggle is on). Delivery runs on a background
    thread by default so the run engine never blocks on the POST; tests flip
    ``WEBHOOK_DELIVERY_ASYNC`` off for inline, deterministic delivery. Returns
    the payload that was sent, or ``None`` when it stayed silent.
    """
    from app.db.session import session_scope

    try:
        with session_scope() as session:
            config = get_config(session)
            if not config.enabled or not config.urls:
                return None  # webhook off / no sink
            request = session.get(RunRequest, request_id)
            if request is None or request.status not in TERMINAL_STATUSES:
                return None  # only completed runs push
            fired = run_findings(session, request_id)
            if not fired and not config.fire_on_all_completions:
                return None  # all-green + not widened → silent (ADR-0016 §3)
            payload = build_payload(session, request, fired)
            urls = list(config.urls)
    except Exception as exc:  # noqa: BLE001 - the nudge must never break a run
        logger.warning("Run-completion webhook emit failed (non-fatal): %s", exc)
        return None

    if get_settings().WEBHOOK_DELIVERY_ASYNC:
        threading.Thread(target=deliver, args=(urls, payload), daemon=True).start()
    else:
        deliver(urls, payload)
    return payload
