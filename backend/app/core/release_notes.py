"""Release notes — what changed in each shipped version, newest first.

The notes ship **in the repo** as ``RELEASE_NOTES.md`` (readable on GitHub as
is) and are rendered read-only on Admin & Settings. They describe the code, so
they live with the code — not in Lakebase, and not editable at runtime.

The file is constrained Markdown, parsed here:

* ``## vX.Y.Z — YYYY-MM-DD`` starts a release (``—`` or ``-`` separator);
* paragraph text before the first section is that release's summary;
* ``### Heading`` starts a section; each ``- `` bullet is one note, and an
  indented line continues the bullet above it.

Anything before the first release heading is the file's own preamble and is
ignored. A malformed file is a hard error (``ReleaseNotesError``) rather than a
silently half-rendered page.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from pathlib import Path

RELEASE_NOTES_PATH = Path(__file__).resolve().parents[3] / "RELEASE_NOTES.md"

_RELEASE = re.compile(r"^## v(\d+)\.(\d+)\.(\d+)\s+[—-]\s+(\d{4}-\d{2}-\d{2})\s*$")


class ReleaseNotesError(ValueError):
    """``RELEASE_NOTES.md`` doesn't follow the shape the parser expects."""


@dataclass
class ReleaseSection:
    title: str
    items: list[str] = field(default_factory=list)


@dataclass
class Release:
    version: str
    date: str
    summary: str = ""
    sections: list[ReleaseSection] = field(default_factory=list)

    @property
    def sort_key(self) -> tuple[int, ...]:
        return tuple(int(p) for p in self.version.split("."))

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "date": self.date,
            "summary": self.summary,
            "sections": [{"title": s.title, "items": s.items} for s in self.sections],
        }


def parse_release_notes(text: str) -> list[Release]:
    """Parse release-notes Markdown into releases, newest version first."""
    releases: list[Release] = []
    current: Release | None = None
    summary: list[str] = []

    def close() -> None:
        if current is not None:
            current.summary = " ".join(summary)
            if not current.sections:
                raise ReleaseNotesError(f"v{current.version} has no sections")

    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if line.startswith("## "):
            m = _RELEASE.match(line)
            if not m:
                raise ReleaseNotesError(f"line {n}: expected '## vX.Y.Z — YYYY-MM-DD', got {line!r}")
            close()
            current = Release(version=".".join(m.group(1, 2, 3)), date=m.group(4))
            summary = []
            releases.append(current)
            continue
        if current is None or not line.strip():
            continue
        if line.startswith("### "):
            current.sections.append(ReleaseSection(title=line[4:].strip()))
        elif line.startswith("- "):
            if not current.sections:
                raise ReleaseNotesError(f"line {n}: bullet before any '### ' section in v{current.version}")
            current.sections[-1].items.append(line[2:].strip())
        elif raw[:1].isspace() and current.sections and current.sections[-1].items:
            current.sections[-1].items[-1] += " " + line.strip()
        elif not current.sections:
            summary.append(line.strip())
        else:
            raise ReleaseNotesError(f"line {n}: stray text inside a section of v{current.version}")
    close()

    versions = [r.version for r in releases]
    dupes = sorted({v for v in versions if versions.count(v) > 1})
    if dupes:
        raise ReleaseNotesError(f"duplicate release(s): {', '.join(dupes)}")
    return sorted(releases, key=lambda r: r.sort_key, reverse=True)


@functools.lru_cache(maxsize=1)
def load_release_notes() -> tuple[Release, ...]:
    """The shipped release notes, parsed once per process (the file only
    changes with a deploy)."""
    return tuple(parse_release_notes(RELEASE_NOTES_PATH.read_text(encoding="utf-8")))
