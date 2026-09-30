"""The reader pages write prose in the words the house style and CONTEXT.md set.

The 0.8.1 docs audit found 32 lines of em dashes left in the pages after
0.8.1 took them out of crapkit's own messages, pages calling a ratchet mark an
exemption where CONTEXT.md says a mark pardons, "the default target of 6" where
the page means the ceiling, "an old unreadable file" for a file no reader
parsed, and sixteen lines, the upgrade guide's title among them, spelling the
tool "Crapkit". Each test reads the prose of every reader page, code left out,
so a quoted message or a code name never trips it.
"""
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("README.md", "CONTEXT.md", "CONTRIBUTING.md", "AGENTS.md", "SECURITY.md",
         "docs/*.md", "docs/handbook.html", "plugin/skills/*/*.md")
_FENCE = re.compile(r"^```.*?^```", re.M | re.S)
_CODE = re.compile(r"`[^`\n]*`|<code>.*?</code>|<pre\b.*?</pre>", re.S)
_DEPLOY_KEYS = ("tests/deploy/test_commit_gate.py", "tests/deploy/test_launcher_lock.py",
                "tests/deploy/test_merge_driver.py", "tests/deploy/test_precommit.py")


def _pages() -> list[Path]:
    return sorted(path for pattern in PAGES for path in ROOT.glob(pattern))


@lru_cache(maxsize=None)
def _prose(path: Path) -> str:
    """The page with its fences and code spans blanked."""
    return _CODE.sub("``", _FENCE.sub("", path.read_text(encoding="utf-8")))


def _lines_matching(pattern: str, flags: int = 0) -> list[str]:
    found = re.compile(pattern, flags)
    return [f"{path.relative_to(ROOT).as_posix()}: {line.strip()[:90]}"
            for path in _pages() for line in _prose(path).splitlines() if found.search(line)]


def test_the_pages_are_read():
    names = {path.relative_to(ROOT).as_posix() for path in _pages()}

    assert {"README.md", "docs/upgrading.md", "docs/lanes.md", "docs/handbook.html",
            "plugin/skills/crapkit-recover/SKILL.md"} <= names


def test_no_page_writes_an_em_dash_in_prose():
    assert _lines_matching("—|&mdash;") == []


def test_the_pages_say_a_mark_pardons_rather_than_exempts():
    avoided = [line for line in _lines_matching(r"\bexempt", re.I) if "_Avoid_: exemption" not in line]

    assert "_Avoid_: exemption" in (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    assert avoided == []


def test_the_pages_name_the_ceiling_and_the_unanalyzable_file():
    assert _lines_matching(r"default target of \d") == []
    assert _lines_matching(r"\bunreadable file") == []


def test_the_pages_write_crapkit_in_lower_case():
    assert _lines_matching(r"\bCrapkit\b") == []


def test_the_upgrade_guide_and_its_deploy_keys_share_the_title():
    title = (ROOT / "docs/upgrading.md").read_text(encoding="utf-8").splitlines()[0]

    keyed = [key for key in _DEPLOY_KEYS if '"Upgrading crapkit"' in (ROOT / key).read_text(encoding="utf-8")]

    assert title == "# Upgrading crapkit"
    assert keyed == list(_DEPLOY_KEYS)
