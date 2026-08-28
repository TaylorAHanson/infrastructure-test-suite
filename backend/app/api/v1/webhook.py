"""The run-completion webhook sink config surface (ADR-0016).

The webhook is the platform's only push: a single generic outbound POST fired by
the run engine on completion (see :mod:`app.services.webhook`). This surface is
just the **sink config** — read it, and edit it. Editing is open to any
authenticated human with **no ceremony** (the sink can't defeat a safety gate)
but every edit is **audited** as a config-plane mutation (ADR-0010 / ADR-0014).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.db.session import get_session
from app.schemas.schemas import WebhookConfigOut, WebhookConfigUpdate
from app.services import webhook

router = APIRouter()


@router.get("/config", response_model=WebhookConfigOut)
def get_webhook_config(session: Session = Depends(get_session)) -> WebhookConfigOut:
    """The current sink config (created **off** on first read)."""
    return WebhookConfigOut.model_validate(webhook.get_config(session).model_dump())


@router.put("/config", response_model=WebhookConfigOut)
def update_webhook_config(
    payload: WebhookConfigUpdate,
    session: Session = Depends(get_session),
    actor: str = Query("api-user", description="Host App identity of the editor"),
) -> WebhookConfigOut:
    """Edit the sink config; the change is audited (``webhook.config.change``)."""
    config = webhook.update_config(
        session, actor=actor, urls=payload.urls, enabled=payload.enabled,
        fire_on_all_completions=payload.fire_on_all_completions,
    )
    return WebhookConfigOut.model_validate(config.model_dump())
