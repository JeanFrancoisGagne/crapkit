"""The CHANGELOG leaves the next release's heading undated until the release tool dates it.

`release.py check` and `bump` look for exactly one `## X.Y.Z — unreleased`
heading and write the date themselves. The 0.8.0 entry landed as `## 0.8.0 —
2026-09-23` while pyproject still said 0.7.6, and the check refused the release
with `'## 0.8.0 — unreleased' x0 (expected 1)`. After the bump the heading
carries the version pyproject ships, and a dated heading is right again.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "release"))

release = pytest.importorskip("release")

DASH = chr(0x2014)


def _newest_heading() -> tuple[str, str]:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    found = re.search(rf"^## (\S+) {DASH} (.+)$", text, re.M)
    assert found, "CHANGELOG.md has no release heading"
    return found.group(1), found.group(2)


def test_a_heading_newer_than_the_shipped_version_says_unreleased():
    version, tail = _newest_heading()
    shipped = release.current_version(ROOT)

    if release._parse(version) > release._parse(shipped):
        assert tail == "unreleased", f"## {version} {DASH} {tail} (pyproject ships {shipped})"
        assert "CHANGELOG.md" not in " ".join(release.check(ROOT, version).problems)
    else:
        assert version == shipped
