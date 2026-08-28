"""Durable audit logging of config-plane mutations (ADR-0014)."""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

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
    request_source: str = "API",
    commit: bool = True,
) -> AuditEvent:
    event = AuditEvent(
        actor=actor, actor_kind=actor_kind, action=action, entity_type=entity_type,
        entity_id=entity_id, summary=summary, before=before or {}, after=after or {},
        request_source=request_source,
    )
    session.add(event)
    if commit:
        session.commit()
    return event


def version_pointer(entity_type: str, entity_id: str, version: int) -> dict:
    """A pointer to a versioned entity's snapshot rather than a duplicated diff.

    Test (and other versioned) create/edit records store this instead of the
    full before→after body (ADR-0014 §3): the immutable snapshot already lives
    in :class:`EntityVersion`, so the audit record only needs to name the
    version it points at.
    """
    return {"entity_type": entity_type, "entity_id": entity_id, "version": version}
