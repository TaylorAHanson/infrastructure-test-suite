"""HTTP hardening: CORS never pairs credentials with a wildcard origin, responses
are compressed, fingerprinted assets are cached for good while index.html is
revalidated, and an uploaded SVG can't run script in the app's origin."""
from __future__ import annotations

import base64

import pytest

from app.main import STATIC_DIR


def test_a_wildcard_origin_never_gets_credentials(client):
    resp = client.get("/api/v1/branding", headers={"Origin": "https://elsewhere.example", "Cookie": "s=1"})
    assert resp.headers.get("access-control-allow-origin") == "*"
    assert "access-control-allow-credentials" not in resp.headers


def test_large_responses_are_compressed(client):
    resp = client.get("/api/v1/tests", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"


def test_fingerprinted_assets_are_immutable_and_the_shell_revalidates(client):
    assets = sorted((STATIC_DIR / "assets").glob("*")) if (STATIC_DIR / "assets").exists() else []
    if not assets:
        pytest.skip("frontend not built")
    asset = client.get(f"/assets/{assets[0].name}")
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_an_uploaded_svg_is_served_without_script(client):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    data_url = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
    assert client.patch("/api/v1/branding", json={"logo": data_url}).status_code == 200
    logo = client.get("/api/v1/branding/logo")
    assert logo.status_code == 200
    assert logo.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in logo.headers["content-security-policy"]
