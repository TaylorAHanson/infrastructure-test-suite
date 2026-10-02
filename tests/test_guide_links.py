"""Every link in the Guide tab's pages lands somewhere real.

The Guide (src/guide/content.ts) renders docs/*.md, docs/ci/*.md, docs/adr/*.md
and CONTEXT.md. A relative link to another page, a heading on it, or a repo file
must resolve; heading anchors use the same slugs the Guide gives its headings.
"""

import re
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = sorted(
    [ROOT / "CONTEXT.md"]
    + list((ROOT / "docs").glob("*.md"))
    + list((ROOT / "docs" / "ci").glob("*.md"))
    + list((ROOT / "docs" / "adr").glob("*.md"))
)
# docs/glossary.md is a pointer; the Guide's glossary page is CONTEXT.md.
ALIASES = {ROOT / "docs" / "glossary.md": ROOT / "CONTEXT.md"}

_SPECIAL = re.compile(r"""[\s~`!@#$%^&*()\-_+=\[\]{}|\\;:"'“”‘’<>,.?/]+""")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[\u0300-\u036f]", "", text)
    text = _SPECIAL.sub("-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return re.sub(r"^(\d)", r"_\1", text).lower()


def _prose_lines(path: Path):
    fenced = False
    text = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.S)
    for line in text.splitlines():
        if re.match(r"\s*(```|~~~)", line):
            fenced = not fenced
            continue
        if not fenced:
            yield line


def heading_slugs(path: Path) -> list[str]:
    out = []
    for line in _prose_lines(path):
        m = re.match(r"(#{1,6})\s+(.+?)\s*#*$", line)
        if not m:
            continue
        text = re.sub(r"`([^`]*)`", r"\1", m.group(2))
        text = re.sub(r"\*\*([^*]+)\*\*|\*([^*]+)\*", lambda g: g.group(1) or g.group(2), text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        out.append(slugify(text))
    return out


def links(path: Path):
    for line in _prose_lines(path):
        line = re.sub(r"`[^`]*`", "", line)
        for m in _LINK.finditer(line):
            yield m.group(1)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_link_resolves(page: Path):
    broken = []
    for href in links(page):
        if re.match(r"[a-z][a-z0-9+.-]*:", href, re.I) or href.startswith("/"):
            continue
        target, _, anchor = href.partition("#")
        dest = (page.parent / target).resolve() if target else page
        dest = ALIASES.get(dest, dest)
        if not dest.exists():
            broken.append(f"{href} (no such file)")
        elif anchor and dest.suffix == ".md" and anchor not in heading_slugs(dest):
            broken.append(f"{href} (no heading #{anchor})")
    assert not broken, f"{page.relative_to(ROOT)} has broken links: {broken}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_headings_are_unique_so_anchors_are_unambiguous(page: Path):
    slugs = heading_slugs(page)
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    assert not dupes, f"{page.relative_to(ROOT)} repeats headings: {dupes}"
