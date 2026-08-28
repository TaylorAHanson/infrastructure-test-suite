"""Branding record: read/seed/update the one global presentation row (ADR-0013).

The service owns three things the API stays thin over:

* **Seed-on-read** — :func:`get_or_seed` returns the singleton, materializing it
  from the repo defaults if absent, so an un-branded install always renders a
  finished record (ADR-0013 §5).
* **Closed-schema validation** — a theme is validated against the closed token
  set + closed colour grammar, and blobs against the ≤ 1 MB PNG/SVG constraint,
  before anything is written (:mod:`app.core.branding`).
* **Audit** — every edit is recorded as an audited config mutation (ADR-0013 §6);
  presentation-only, so **no** friction ceremony.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from app.core.branding import (
    DEFAULT_APP_NAME,
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_FOOTER_TEXT,
    DEFAULT_THEME,
    SINGLETON_ID,
    BrandingValidationError,
    merge_theme,
    validate_blob,
    validate_theme,
)
from app.core.enums import AuditAction
from app.core.ids import utcnow
from app.models import Branding
from app.schemas.schemas import BrandingOut, BrandingUpdate
from app.services.audit import record_audit

__all__ = ["get_or_seed", "to_out", "apply_update", "BrandingValidationError"]


def get_or_seed(session: Session) -> Branding:
    """Return the single global branding row, seeding defaults if it's absent.

    Idempotent by the fixed singleton id — concurrent boots/reads converge on one
    row. This is why an install with no explicit branding still looks finished.
    """
    row = session.get(Branding, SINGLETON_ID)
    if row is not None:
        return row
    row = Branding(
        id=SINGLETON_ID,
        app_name=DEFAULT_APP_NAME,
        assistant_name=DEFAULT_ASSISTANT_NAME,
        footer_text=DEFAULT_FOOTER_TEXT,
        theme=DEFAULT_THEME,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def to_out(row: Branding) -> BrandingOut:
    """Project the row onto the public read model (blobs served separately)."""
    return BrandingOut(
        app_name=row.app_name,
        assistant_name=row.assistant_name,
        footer_text=row.footer_text,
        theme=row.theme or DEFAULT_THEME,
        has_logo=row.logo is not None,
        logo_content_type=row.logo_content_type,
        has_favicon=row.favicon is not None,
        favicon_content_type=row.favicon_content_type,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


def _audit_snapshot(row: Branding) -> dict:
    """Presentation-only snapshot for the audit trail — never the blob bytes."""
    return {
        "app_name": row.app_name,
        "assistant_name": row.assistant_name,
        "footer_text": row.footer_text,
        "theme": row.theme,
        "has_logo": row.logo is not None,
        "has_favicon": row.favicon is not None,
    }


def apply_update(session: Session, payload: BrandingUpdate, *, actor: str) -> Branding:
    """Validate + apply a branding edit, then record it in the audit trail.

    Raises :class:`BrandingValidationError` (before any write) on a bad theme or
    blob. On success the change is instant-apply: the next shell read/poll sees it.
    """
    row = get_or_seed(session)
    before = _audit_snapshot(row)

    data = payload.model_dump(exclude_unset=True)

    # Theme first, so a bad theme rejects the whole edit before any write. The
    # incoming (possibly partial) theme is validated, then merged onto the current
    # palette and backfilled to the complete closed set — a partial edit never
    # drops a token or leaves the stored palette incomplete (ADR-0013 §2).
    if payload.theme is not None:
        validated = validate_theme(payload.theme)
        row.theme = merge_theme(row.theme, validated, accent_provided="accent" in payload.theme)

    # Blobs: an explicit clear wins; otherwise a provided data: URL is validated.
    if payload.clear_logo:
        row.logo, row.logo_content_type = None, None
    elif payload.logo is not None:
        decoded = validate_blob(payload.logo)
        if decoded is not None:
            row.logo, row.logo_content_type = decoded

    if payload.clear_favicon:
        row.favicon, row.favicon_content_type = None, None
    elif payload.favicon is not None:
        decoded = validate_blob(payload.favicon)
        if decoded is not None:
            row.favicon, row.favicon_content_type = decoded

    for field in ("app_name", "assistant_name", "footer_text"):
        if field in data and data[field] is not None:
            setattr(row, field, data[field])

    row.updated_at = utcnow()
    row.updated_by = actor
    session.add(row)
    session.commit()
    session.refresh(row)

    record_audit(
        session, actor=actor, action=AuditAction.BRANDING_EDIT.value,
        entity_type="branding", entity_id=SINGLETON_ID,
        summary=f"Branding updated by {actor}",
        before=before, after=_audit_snapshot(row),
    )
    return row


def logo_bytes(session: Session) -> Optional[tuple[bytes, str]]:
    row = get_or_seed(session)
    if row.logo is None:
        return None
    return row.logo, row.logo_content_type or "application/octet-stream"


def favicon_bytes(session: Session) -> Optional[tuple[bytes, str]]:
    row = get_or_seed(session)
    if row.favicon is None:
        return None
    return row.favicon, row.favicon_content_type or "application/octet-stream"
