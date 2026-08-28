"""HTTP API seam for branding (ADR-0013, #38).

Drives the real contract through ``TestClient`` + SQLite. Asserts the acceptance
criteria as external behaviour: a single global record with a closed schema (an
unknown field is rejected); logo/favicon size/type validation; a closed
named-token theme, light/dark-aware; seeded defaults so an un-branded install
renders finished; and that a branding edit is audited (config-plane, no ceremony).
"""
from __future__ import annotations

import base64


def _data_url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def get_branding(client):
    return client.get("/api/v1/branding").json()


def audit(client, **params):
    return client.get("/api/v1/audit", params=params).json()


# -------------------- defaults seeded → finished install ---------------------
def test_unbranded_install_renders_finished_defaults(client):
    body = get_branding(client)
    assert body["app_name"] == "Validation Platform"
    assert body["assistant_name"] == "Assistant"
    # A closed named-token palette, light/dark-aware.
    tokens = body["theme"]["tokens"]
    assert tokens and all("light" in v and "dark" in v for v in tokens.values())
    assert body["has_logo"] is False and body["has_favicon"] is False


# ---------------------- single global record, closed schema ------------------
def test_unknown_field_is_rejected(client):
    resp = client.patch("/api/v1/branding", json={"app_name": "X", "not_a_field": "boom"})
    assert resp.status_code == 422


def test_single_global_record_updates_in_place(client):
    client.patch("/api/v1/branding", json={"app_name": "Acme"})
    client.patch("/api/v1/branding", json={"footer_text": "© Acme"})
    body = get_branding(client)
    # Same record — earlier edit persists alongside the later one.
    assert body["app_name"] == "Acme" and body["footer_text"] == "© Acme"


# ----------------------------- theme validation ------------------------------
def test_theme_closed_token_palette_light_dark(client):
    resp = client.patch("/api/v1/branding", json={
        "theme": {"accent": "#123456", "tokens": {"ink": {"light": "#ffffff", "dark": "#000000"}}},
    })
    assert resp.status_code == 200
    assert resp.json()["theme"]["accent"] == "#123456"


def test_free_form_css_theme_is_rejected(client):
    # Unknown token, unknown field, and non-hex colour are all rejected (no XSS hole).
    assert client.patch("/api/v1/branding", json={"theme": {"tokens": {"evil": {"light": "#fff", "dark": "#000"}}}}).status_code == 422
    assert client.patch("/api/v1/branding", json={"theme": {"style": "body{}"}}).status_code == 422
    assert client.patch("/api/v1/branding", json={"theme": {"accent": "url(x)"}}).status_code == 422


# --------------------------- blob size / type --------------------------------
def test_logo_wrong_type_rejected(client):
    resp = client.patch("/api/v1/branding", json={"logo": _data_url("image/gif", b"GIF89a")})
    assert resp.status_code == 422


def test_logo_over_1mb_rejected(client):
    big = _data_url("image/png", b"x" * (1024 * 1024 + 1))
    resp = client.patch("/api/v1/branding", json={"logo": big})
    assert resp.status_code == 422


def test_logo_upload_served_from_blob_route(client):
    png = b"\x89PNG\r\n\x1a\n" + b"z" * 200
    resp = client.patch("/api/v1/branding", json={"logo": _data_url("image/png", png)})
    assert resp.status_code == 200
    assert resp.json()["has_logo"] is True

    logo = client.get("/api/v1/branding/logo")
    assert logo.status_code == 200
    assert logo.headers["content-type"].startswith("image/png")
    assert logo.content == png


def test_favicon_absent_is_404(client):
    assert client.get("/api/v1/branding/favicon").status_code == 404


# --------------------------------- audited -----------------------------------
def test_branding_edit_is_audited(client):
    client.patch("/api/v1/branding?actor=carol", json={"app_name": "Audited Co"})
    events = audit(client, event_type="branding.edit")
    assert len(events) == 1
    e = events[0]
    assert e["actor"] == "carol"
    assert e["entity_type"] == "branding"
    assert e["after"]["app_name"] == "Audited Co"
    # Presentation snapshot only — never the blob bytes.
    assert "logo" not in e["after"] and e["after"]["has_logo"] is False


def test_branding_edit_shows_in_default_audit_view(client):
    client.patch("/api/v1/branding", json={"assistant_name": "Helper"})
    assert "branding.edit" in {e["action"] for e in audit(client)}
    # It's a config-plane event type the filter dropdown offers.
    assert "branding.edit" in client.get("/api/v1/audit/event-types").json()
