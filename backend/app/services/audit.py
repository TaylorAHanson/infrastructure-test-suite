"""Durable audit logging.

Every mutating action (suite/test/schedule changes, run triggers, config) is
recorded as an :class:`AuditEvent` row. These rows are the durable record;
stdout logs are ephemeral on Databricks Apps.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

from app.core.enums import RequestSource
from app.models import AuditEvent

logger = logging.getLogger(__name__)


def record_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    actor_kind: str = "user",
    summary: str = "",
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    request_source: RequestSource = RequestSource.API,
    commit: bool = True,
) -> AuditEvent:
    event = AuditEvent(
        actor=actor,
        actor_kind=actor_kind,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        before=before or {},
        after=after or {},
        request_source=request_source,
    )
    session.add(event)
    if commit:
        session.commit()
    logger.info(
        "audit",
        extra={"actor": actor, "run_id": entity_id if entity_type == "run" else None},
    )
    return event
