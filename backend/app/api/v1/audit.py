"""Read-only audit view: list / filter / export config-plane mutations (ADR-0014).

The audit log is the **standing forensic control** that makes the no-roles
access model (ADR-0010) defensible, so this surface is **read-only by
construction**: it exposes only ``GET`` routes — there is no path here (nor
anywhere) that edits or deletes an audit record, which is the append-only
integrity property (ADR-0014 §1).

Scope is **config-plane mutations only** (:data:`CONFIG_PLANE_ACTIONS`): runs
(``run.trigger``) and run-time blocks (``safety.block``) are filtered out at the
query, so the view is a config-mutation log, not a run log. Records are
filterable by **target · actor · event type** and **exportable** (CSV/JSON).
"""
from __future__ import annotations

import csv
import io
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from app.core.enums import CONFIG_PLANE_ACTIONS
from app.db.session import get_session
from app.models import AuditEvent

router = APIRouter()

#: The columns an exported CSV record carries, in order. ``before``/``after``
#: are kept (JSON-encoded) so a CSV export stays forensically complete — the
#: downgrade flag and the override's ceremony reason live there.
_EXPORT_FIELDS = ["id", "ts", "actor", "actor_kind", "action", "entity_type",
                  "entity_id", "summary", "before", "after"]


def _query(
    session: Session,
    *,
    target: Optional[str],
    actor: Optional[str],
    event_type: Optional[str],
    limit: int,
) -> list[AuditEvent]:
    """The one filter path shared by list + export.

    Only config-plane event types are ever returned — the ``in_`` clause is the
    runs-excluded guarantee. Newest first, so the view reads as a timeline.
    """
    stmt = select(AuditEvent).where(AuditEvent.action.in_(CONFIG_PLANE_ACTIONS))  # type: ignore[attr-defined]
    if target:
        # A target's history: the events whose entity is that target.
        stmt = stmt.where(AuditEvent.entity_id == target)
    if actor:
        stmt = stmt.where(AuditEvent.actor == actor)
    if event_type:
        stmt = stmt.where(AuditEvent.action == event_type)
    stmt = stmt.order_by(AuditEvent.ts.desc()).limit(limit)  # type: ignore[attr-defined]
    return list(session.exec(stmt).all())


@router.get("")
def list_audit(
    session: Session = Depends(get_session),
    target: Optional[str] = Query(None, description="Entity id of a target to scope to"),
    actor: Optional[str] = Query(None, description="Host App identity of the actor"),
    event_type: Optional[str] = Query(None, description="Audit event type (action)"),
    limit: int = Query(500, ge=1, le=5000),
) -> list[AuditEvent]:
    """List config-plane audit records, filterable by target · actor · event type."""
    return _query(session, target=target, actor=actor, event_type=event_type, limit=limit)


@router.get("/event-types")
def audit_event_types() -> list[str]:
    """The closed set of config-plane event types, for a filter dropdown."""
    return sorted(CONFIG_PLANE_ACTIONS)


@router.get("/export")
def export_audit(
    session: Session = Depends(get_session),
    target: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(5000, ge=1, le=50000),
    format: str = Query("csv", pattern="^(csv|json)$"),
) -> Response:
    """Export the (filtered) records as a downloadable CSV or JSON file.

    Same filter contract as :func:`list_audit`; an adopter can also point their
    own query straight at the Lakebase table (ADR-0014 §5)."""
    rows = _query(session, target=target, actor=actor, event_type=event_type, limit=limit)

    if format == "json":
        body = json.dumps([r.model_dump(mode="json") for r in rows], indent=2, default=str)
        return Response(
            content=body, media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="audit-log.json"'},
        )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        row = r.model_dump(mode="json")
        # Flatten the nested dicts so a spreadsheet cell holds valid JSON.
        row["before"] = json.dumps(row.get("before") or {})
        row["after"] = json.dumps(row.get("after") or {})
        writer.writerow(row)
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )
