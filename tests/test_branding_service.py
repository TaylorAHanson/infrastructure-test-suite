"""Service-layer seam for branding (ADR-0013, #38).

Unit-tests the branding validator and the seed/apply service directly: the
closed token set, the closed colour grammar (no free-form CSS/JS), blob size/type
constraints, seed-on-read defaults, and instant-apply on next read. HTTP wiring
and audit live in ``test_branding_api.py``.
"""
from __future__ import annotations

import base64

import pytest

from app.core import branding as b
from app.schemas.schemas import BrandingUpdate
from app.services import branding as svc


# ------------------------------ theme validator ------------------------------
def test_default_theme_is_valid_and_light_dark_aware():
    theme = b.validate_theme(b.DEFAULT_THEME)
    # Every token resolves in both schemes (ADR-0013 §2).
    for name in b.BRAND_TOKEN_NAMES:
        assert theme["tokens"][name]["light"]
        assert theme["tokens"][name]["dark"]


def test_unknown_theme_token_is_rejected():
    with pytest.raises(b.BrandingValidationError):
        b.validate_theme({"accent": "#ff0000", "tokens": {"not_a_token": {"light": "#fff", "dark": "#000"}}})


def test_unknown_theme_field_is_rejected():
    with pytest.raises(b.BrandingValidationError):
        b.validate_theme({"accent": "#ff0000", "custom_css": "body{display:none}"})


def test_non_hex_colour_is_rejected_no_free_form_css():
    # A free-form value (url()/expression/script) is not a hex colour → rejected.
    with pytest.raises(b.BrandingValidationError):
        b.validate_theme({"accent": "url(javascript:alert(1))"})
    with pytest.raises(b.BrandingValidationError):
        b.validate_theme({"tokens": {"ink": {"light": "red; }</style><script>", "dark": "#000"}}})


def test_accent_and_token_hex_accepted():
    theme = b.validate_theme({"accent": "#abc", "tokens": {"ink": {"light": "#ffffff", "dark": "#000000"}}})
    assert theme["accent"] == "#abc"
    assert theme["tokens"]["ink"] == {"light": "#ffffff", "dark": "#000000"}


# ------------------------------ blob validator -------------------------------
def _data_url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def test_png_and_svg_blobs_accepted():
    png = _data_url("image/png", b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    raw, mime = b.validate_blob(png)
    assert mime == "image/png" and raw

    svg = _data_url("image/svg+xml", b"<svg/>")
    raw, mime = b.validate_blob(svg)
    assert mime == "image/svg+xml"


def test_wrong_type_blob_rejected():
    with pytest.raises(b.BrandingValidationError):
        b.validate_blob(_data_url("image/gif", b"GIF89a"))
    with pytest.raises(b.BrandingValidationError):
        b.validate_blob(_data_url("application/javascript", b"alert(1)"))


def test_oversize_blob_rejected():
    too_big = _data_url("image/png", b"x" * (b.MAX_BLOB_BYTES + 1))
    with pytest.raises(b.BrandingValidationError):
        b.validate_blob(too_big)


def test_blob_at_limit_accepted():
    at_limit = _data_url("image/png", b"x" * b.MAX_BLOB_BYTES)
    raw, _ = b.validate_blob(at_limit)
    assert len(raw) == b.MAX_BLOB_BYTES


def test_none_blob_is_noop():
    assert b.validate_blob(None) is None


# --------------------------- seed / apply service ----------------------------
def test_get_or_seed_returns_finished_defaults(session):
    row = svc.get_or_seed(session)
    assert row.app_name == b.DEFAULT_APP_NAME
    assert row.assistant_name == b.DEFAULT_ASSISTANT_NAME
    assert row.theme["accent"] == b.DEFAULT_THEME["accent"]
    # Idempotent: a second call returns the same singleton, not a duplicate.
    again = svc.get_or_seed(session)
    assert again.id == row.id == b.SINGLETON_ID


def test_apply_update_is_instant_apply_on_next_read(session):
    svc.apply_update(session, BrandingUpdate(app_name="Acme Validation"), actor="alice")
    out = svc.to_out(svc.get_or_seed(session))
    assert out.app_name == "Acme Validation"
    assert out.updated_by == "alice"


def test_partial_theme_edit_keeps_palette_complete_and_coherent(session):
    # Customize one token…
    svc.apply_update(session, BrandingUpdate(
        theme={"tokens": {"panel": {"light": "#eeeeee", "dark": "#123123"}}}), actor="a")
    # …then a later edit that only touches the accent must NOT drop the custom
    # token, and the stored palette must still carry the whole closed set.
    svc.apply_update(session, BrandingUpdate(theme={"accent": "#00ff00"}), actor="a")

    theme = svc.get_or_seed(session).theme
    assert theme["accent"] == "#00ff00"
    assert theme["tokens"]["panel"] == {"light": "#eeeeee", "dark": "#123123"}
    # Every token in the closed set is present (never a partial palette).
    assert set(theme["tokens"]) == set(b.BRAND_TOKEN_NAMES)


def test_partial_theme_edit_without_accent_preserves_current_accent(session):
    svc.apply_update(session, BrandingUpdate(theme={"accent": "#abcdef"}), actor="a")
    svc.apply_update(session, BrandingUpdate(
        theme={"tokens": {"ink": {"light": "#ffffff", "dark": "#000000"}}}), actor="a")
    assert svc.get_or_seed(session).theme["accent"] == "#abcdef"


def test_apply_update_stores_and_clears_blob(session):
    png = _data_url("image/png", b"\x89PNG" + b"y" * 50)
    svc.apply_update(session, BrandingUpdate(logo=png), actor="bob")
    assert svc.logo_bytes(session) is not None
    out = svc.to_out(svc.get_or_seed(session))
    assert out.has_logo and out.logo_content_type == "image/png"

    svc.apply_update(session, BrandingUpdate(clear_logo=True), actor="bob")
    assert svc.logo_bytes(session) is None
