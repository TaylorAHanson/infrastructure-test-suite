"""Branding endpoints: runtime white-label read/update (ADR-0013).

The shell reads ``GET /branding`` on load and the existing poll refreshes it, so
a change is instant-apply within the poll interval — no redeploy, no push infra
(ADR-0013 §4). Editing is open to **any authenticated human with no friction
ceremony** (presentation can't defeat a safety gate, ADR-0010/§6) but every edit
is **audited** like other config mutations.

Logo/favicon blobs are served from their own routes (kept out of the JSON poll
payload) with their stored MIME type; ``<img>`` is the only consumer, so an SVG
served here cannot execute script.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import Session

from app.api.deps import actor_handle
from app.core.branding import BrandingValidationError
from app.db.session import get_session
from app.schemas.schemas import BrandingOut, BrandingUpdate
from app.services import branding as branding_service

router = APIRouter()


@router.get("", response_model=BrandingOut)
def get_branding(session: Session = Depends(get_session)) -> BrandingOut:
    """Read the global branding record (seeded from defaults if never set)."""
    return branding_service.to_out(branding_service.get_or_seed(session))


@router.patch("", response_model=BrandingOut)
def update_branding(
    payload: BrandingUpdate,
    session: Session = Depends(get_session),
    actor: str = Depends(actor_handle()),
) -> BrandingOut:
    """Edit the branding record. Closed-schema (unknown field → 422 by the schema);
    a bad theme/blob → 422; the edit is audited."""
    try:
        row = branding_service.apply_update(session, payload, actor=actor)
    except BrandingValidationError as exc:
        raise HTTPException(422, str(exc))
    return branding_service.to_out(row)


def _blob_response(pair, cache: str = "no-cache") -> Response:
    raw, content_type = pair
    headers = {"Cache-Control": cache, "X-Content-Type-Options": "nosniff"}
    if "svg" in (content_type or ""):
        # An SVG opened directly is a document that can run script in the app's origin.
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return Response(content=raw, media_type=content_type, headers=headers)


@router.get("/logo")
def get_logo(session: Session = Depends(get_session)) -> Response:
    """Serve the logo blob, or 404 when none is set (the shell falls back to the
    text wordmark)."""
    pair = branding_service.logo_bytes(session)
    if pair is None:
        raise HTTPException(404, "no logo set")
    return _blob_response(pair)


@router.get("/favicon")
def get_favicon(session: Session = Depends(get_session)) -> Response:
    """Serve the favicon blob, or 404 when none is set."""
    pair = branding_service.favicon_bytes(session)
    if pair is None:
        raise HTTPException(404, "no favicon set")
    return _blob_response(pair)
