"""Branding: the closed presentation schema and its validators (ADR-0013).

Branding is a **runtime white-label layer**, and the discipline that keeps it
*presentation only, never behaviour* is that everything here is **closed**:

* a **closed set of design-token names** (:data:`BRAND_TOKEN_NAMES`) — a theme may
  only paint these; an unknown token is rejected. Adding a token is a code change
  here, never a runtime "add a setting."
* a **closed colour grammar** — every colour (the accent and every token, in both
  light and dark) must be a plain ``#rgb`` / ``#rrggbb`` hex string. This is the
  wall against free-form CSS/JS: there is no place a theme can smuggle a
  ``url(...)`` / ``expression(...)`` / ``<script>`` past, so the XSS/accessibility
  hole a free-form theme would open never exists.
* **closed blob constraints** — logo/favicon are ≤ 1 MB PNG/SVG blobs.

The default palette is seeded from the app's own design system (the Tailwind
token palette in ``tailwind.config.js`` / ``src/index.css``) so an un-branded
install renders finished, in both light and dark.
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Optional

#: The closed set of themeable design-token names (everything the accent is not).
#: These mirror the app's Tailwind custom-colour palette; the theme paints them
#: as ``--brand-<name>`` CSS custom properties. An unknown token name is rejected
#: — a new token is a migration here, never a runtime addition (ADR-0013 §1/§2).
BRAND_TOKEN_NAMES: frozenset[str] = frozenset(
    {"ink", "ink_soft", "panel", "edge", "muted", "pass", "fail", "warn", "skip", "info"}
)

#: The default theme, seeded from the design system so both schemes resolve out of
#: the box (ADR-0013 §5). ``accent`` is the single headline brand colour; ``tokens``
#: is the closed palette, each token carrying a ``light`` and a ``dark`` value.
DEFAULT_THEME: dict = {
    "accent": "#ff6b3d",  # Databricks-ish lava
    "tokens": {
        "ink":      {"dark": "#0b1220", "light": "#ffffff"},
        "ink_soft": {"dark": "#1b2435", "light": "#f1f5f9"},
        "panel":    {"dark": "#111a2b", "light": "#f8fafc"},
        "edge":     {"dark": "#22304a", "light": "#d0d7e2"},
        "muted":    {"dark": "#8a97ad", "light": "#64748b"},
        "pass":     {"dark": "#3fb950", "light": "#1a7f37"},
        "fail":     {"dark": "#f85149", "light": "#cf222e"},
        "warn":     {"dark": "#d29922", "light": "#9a6700"},
        "skip":     {"dark": "#6e7681", "light": "#57606a"},
        "info":     {"dark": "#58a6ff", "light": "#0969da"},
    },
}

#: Repo-seeded defaults so an un-branded install looks finished (ADR-0013 §5). The
#: text wordmark (``app_name``) stands in when no logo blob is set.
DEFAULT_APP_NAME = "Validation Platform"
DEFAULT_ASSISTANT_NAME = "Assistant"
DEFAULT_FOOTER_TEXT = "Validation Platform · open source"

#: The single global row's fixed primary key (one record per deployment).
SINGLETON_ID = "global"

#: Blob constraints (ADR-0013 §3): ≤ 1 MB, PNG or SVG only.
MAX_BLOB_BYTES = 1024 * 1024
ALLOWED_IMAGE_TYPES: frozenset[str] = frozenset({"image/png", "image/svg+xml"})

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w.+/-]+);base64,(?P<b64>.+)$", re.DOTALL)


class BrandingValidationError(ValueError):
    """A branding update violated the closed schema / blob constraints."""


def _is_hex_color(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX_COLOR_RE.match(value))


def validate_theme(theme: object) -> dict:
    """Validate a theme against the closed token set + closed colour grammar.

    Returns the normalized theme on success; raises
    :class:`BrandingValidationError` otherwise. Enforces (ADR-0013 §2):

    * only ``accent`` + ``tokens`` at the top level (no free-form keys);
    * ``accent`` is a hex colour;
    * every token name is in :data:`BRAND_TOKEN_NAMES` (unknown → rejected);
    * every token carries a ``light`` and a ``dark`` **hex** colour — nothing that
      could be free-form CSS/JS.
    """
    if not isinstance(theme, dict):
        raise BrandingValidationError("theme must be an object")

    unknown = set(theme) - {"accent", "tokens"}
    if unknown:
        raise BrandingValidationError(f"unknown theme field(s): {', '.join(sorted(unknown))}")

    accent = theme.get("accent", DEFAULT_THEME["accent"])
    if not _is_hex_color(accent):
        raise BrandingValidationError(f"accent must be a hex colour, got {accent!r}")

    raw_tokens = theme.get("tokens", {})
    if not isinstance(raw_tokens, dict):
        raise BrandingValidationError("theme.tokens must be an object")

    unknown_tokens = set(raw_tokens) - BRAND_TOKEN_NAMES
    if unknown_tokens:
        raise BrandingValidationError(
            f"unknown theme token(s): {', '.join(sorted(unknown_tokens))}"
        )

    tokens: dict = {}
    for name, value in raw_tokens.items():
        if not isinstance(value, dict) or set(value) - {"light", "dark"}:
            raise BrandingValidationError(f"token {name!r} must be {{light, dark}}")
        for scheme in ("light", "dark"):
            if scheme in value and not _is_hex_color(value[scheme]):
                raise BrandingValidationError(
                    f"token {name!r}.{scheme} must be a hex colour, got {value[scheme]!r}"
                )
        # Backfill a missing scheme from the default so a token always resolves in both.
        default = DEFAULT_THEME["tokens"].get(name, {})
        tokens[name] = {
            "light": value.get("light", default.get("light")),
            "dark": value.get("dark", default.get("dark")),
        }

    return {"accent": accent, "tokens": tokens}


def merge_theme(current: Optional[dict], incoming: dict, *, accent_provided: bool) -> dict:
    """Merge a validated (possibly partial) theme onto the current palette.

    Keeps the theme a **complete, coherent closed set** (ADR-0013 §2) across
    partial edits: tokens the edit doesn't mention keep their current value (or
    the seeded default if never set), the accent is only changed when the edit
    actually carried one, and every token in :data:`BRAND_TOKEN_NAMES` is present
    in the result. ``incoming`` must already be :func:`validate_theme`-clean.
    """
    current = current or {}
    current_tokens = current.get("tokens") or {}
    tokens: dict = {}
    for name in BRAND_TOKEN_NAMES:
        default = DEFAULT_THEME["tokens"][name]
        tokens[name] = (
            incoming.get("tokens", {}).get(name)
            or current_tokens.get(name)
            or default
        )
    accent = incoming["accent"] if accent_provided else current.get("accent", DEFAULT_THEME["accent"])
    return {"accent": accent, "tokens": tokens}


def validate_blob(data_url: Optional[str]) -> Optional[tuple[bytes, str]]:
    """Parse + validate a logo/favicon ``data:`` URL (ADR-0013 §3).

    Returns ``(raw_bytes, content_type)``; ``None`` when ``data_url`` is ``None``
    (an explicit clear). Rejects a wrong MIME type or a blob over 1 MB.
    """
    if data_url is None:
        return None
    if not isinstance(data_url, str):
        raise BrandingValidationError("blob must be a data: URL string")
    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise BrandingValidationError("blob must be a base64 data: URL")
    mime = m.group("mime").lower()
    if mime not in ALLOWED_IMAGE_TYPES:
        raise BrandingValidationError(
            f"unsupported image type {mime!r}; allowed: PNG, SVG"
        )
    try:
        raw = base64.b64decode(m.group("b64"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BrandingValidationError(f"invalid base64 blob: {exc}") from exc
    if len(raw) > MAX_BLOB_BYTES:
        raise BrandingValidationError(
            f"blob is {len(raw)} bytes; the limit is {MAX_BLOB_BYTES} (1 MB)"
        )
    if not raw:
        raise BrandingValidationError("blob is empty")
    return raw, mime
