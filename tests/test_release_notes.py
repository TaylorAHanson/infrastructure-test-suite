"""Release notes: the shipped ``RELEASE_NOTES.md`` and its Admin endpoint.

Asserts the parser's contract (newest first by semver, summary + sections +
continued bullets, malformed files fail loudly) and that the shipped file stays
in lock-step with ``APP_VERSION`` — so a version bump without a release note,
or a note for a version that isn't shipping, fails CI.
"""
from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.core.release_notes import ReleaseNotesError, load_release_notes, parse_release_notes

SAMPLE = """\
# Release notes

Preamble the parser ignores, including - a dash.

## v1.2.0 — 2026-11-01

Second release.
Summary wraps.

### Added
- New thing with `code`.
- A bullet that
  continues here.

## v1.10.0 - 2027-01-01

Out-of-order on purpose; semver sorts it first.

### Fixed
- One fix.

## v1.0.0 — 2026-10-02

### Added
- First.
"""


def test_parses_releases_newest_first_by_semver():
    releases = parse_release_notes(SAMPLE)
    assert [r.version for r in releases] == ["1.10.0", "1.2.0", "1.0.0"]


def test_parses_summary_sections_and_continued_bullets():
    release = next(r for r in parse_release_notes(SAMPLE) if r.version == "1.2.0")
    assert release.date == "2026-11-01"
    assert release.summary == "Second release. Summary wraps."
    assert release.sections[0].title == "Added"
    assert release.sections[0].items == ["New thing with `code`.", "A bullet that continues here."]


def test_release_without_summary_is_allowed():
    assert parse_release_notes(SAMPLE)[-1].summary == ""


@pytest.mark.parametrize("text", [
    "## 1.0.0 — 2026-10-02\n### A\n- x\n",                     # missing the v
    "## v1.0 — 2026-10-02\n### A\n- x\n",                      # not X.Y.Z
    "## v1.0.0\n### A\n- x\n",                                 # no date
    "## v1.0.0 — 2026-10-02\nSummary.\n",                      # no sections
    "## v1.0.0 — 2026-10-02\n- orphan bullet\n",               # bullet before a section
    "## v1.0.0 — 2026-10-02\n### A\n- x\nstray paragraph\n",   # text inside a section
    "## v1.0.0 — 2026-10-02\n### A\n- x\n## v1.0.0 — 2026-10-03\n### B\n- y\n",  # duplicate
])
def test_malformed_release_notes_fail_loudly(text):
    with pytest.raises(ReleaseNotesError):
        parse_release_notes(text)


def test_shipped_release_notes_parse_and_newest_matches_app_version():
    releases = load_release_notes()
    assert releases, "RELEASE_NOTES.md has no releases"
    assert releases[0].version == get_settings().APP_VERSION
    assert all(s.items for r in releases for s in r.sections)


def test_release_notes_endpoint(client):
    body = client.get("/api/v1/admin/release-notes").json()
    assert body["current_version"] == get_settings().APP_VERSION
    assert body["releases"][0]["version"] == body["current_version"]
    first = body["releases"][0]
    assert set(first) == {"version", "date", "summary", "sections"}
    assert first["sections"][0]["title"] and first["sections"][0]["items"]
