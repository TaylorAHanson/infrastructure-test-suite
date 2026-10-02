"""In-app versioning + rollback (Lakebase-authoritative, ADR-0001).

The store — not YAML, not git — is the source of truth for Checks/Tests/Suites.
Every save snapshots the entity into :class:`EntityVersion`; a rollback restores
a prior snapshot as a new current version.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.models import EntityVersion


def snapshot(session: Session, entity_type: str, entity_id: str, version: int,
             data: dict, actor: str = "system", commit: bool = True) -> EntityVersion:
    """Record an immutable snapshot of an entity at a given version."""
    row = EntityVersion(
        entity_type=entity_type, entity_id=entity_id, version=version,
        snapshot=data, actor=actor,
    )
    session.add(row)
    if commit:
        session.commit()
        session.refresh(row)
    return row


def list_versions(session: Session, entity_type: str, entity_id: str) -> list[EntityVersion]:
    return session.exec(
        select(EntityVersion)
        .where(EntityVersion.entity_type == entity_type, EntityVersion.entity_id == entity_id)
        .order_by(EntityVersion.version)  # type: ignore[arg-type]
    ).all()


def get_version(session: Session, entity_type: str, entity_id: str, version: int) -> Optional[EntityVersion]:
    return session.exec(
        select(EntityVersion).where(
            EntityVersion.entity_type == entity_type,
            EntityVersion.entity_id == entity_id,
            EntityVersion.version == version,
        )
    ).first()
