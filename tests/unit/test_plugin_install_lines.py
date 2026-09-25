"""The plugin install and refresh lines every page prints, held to what was measured.

Measured on Windows with Claude Code 2.1.138 and 2.1.281 and Codex 0.130.0 to
0.156.1, against github.com and against a local mirror:

- A marketplace line with no `--sparse` clones the whole repository tip: 61 MB for
  Claude Code and 69 MB for Codex, where `.claude-plugin` and `plugin` alone take
  0.8 MB and 1.9 MB. One Claude Code add ran into its 120 s clone timeout.
- Codex re-checks every Git marketplace at each start and reinstalls its plugins
  when the marketplace moved, so an unpinned one moved the plugin past a PyPI CLI
  within 18 s of a push to main. With `--ref vX.Y.Z` it is checked against the tag.
- A marketplace added at a tag stays there: `codex plugin marketplace upgrade`
  keeps it, and adding it at another tag is refused until it is removed. Removing it
  keeps the installed plugin, and the add at the new tag leaves the old copy in
  place until `codex plugin add` installs the new one.
- `codex plugin marketplace upgrade` followed by `codex plugin add` repeats an
  install, and on Windows the repeat exits 1 with os error 5 while a file of the old
  copy is open.
- `codex plugin add` first exists in 0.131.0; `codex plugin list --json` in 0.137.0.
"""
from __future__ import annotations

import html
import json
import re
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "release"))

release = pytest.importorskip("release")

SLUG = "JeanFrancoisGagne/crapkit"
URL = f"https://github.com/{SLUG}.git"
CLAUDE_ADD = f"claude plugin marketplace add {SLUG}"
CODEX_ADD = f"codex plugin marketplace add {URL}"
CODEX_INSTALL = "codex plugin add crapkit@crapkit"
CODEX_LIST = "codex plugin list --marketplace crapkit --json"
CODEX_REMOVE = "codex plugin marketplace remove crapkit"
CODEX_UPGRADE = "codex plugin marketplace upgrade crapkit"
PAGES = ("README.md", "docs/adoption.md", "docs/upgrading.md", "docs/handbook.html",
         "plugin/skills/crapkit-onboard/SKILL.md", "tools/release/README.md")
# The release runbook prints the line for any release, so it spells the tag.
RUNBOOK = "tools/release/README.md"
_TAG = re.compile(r"<[^>]+>")
_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.S | re.M)
_PRE = re.compile(r"<pre><code>(.*?)</code></pre>", re.S)


@lru_cache(maxsize=None)
def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _version() -> str:
    return tomllib.loads(_doc("pyproject.toml"))["project"]["version"]


def _sparse() -> tuple[str, ...]:
    """What a marketplace clone needs: the marketplace file's directory and each
    plugin's source directory."""
    listing = json.loads(_doc(".claude-plugin/marketplace.json"))
    sources = [Path(plugin["source"]).as_posix() for plugin in listing["plugins"]]
    return (".claude-plugin", *sources)


def _blocks(name: str) -> list[list[str]]:
    """The command lines of each code block on a page, markup stripped."""
    text = _doc(name)
    found = _PRE.findall(text) if name.endswith(".html") else _FENCE.findall(text)
    return [[html.unescape(_TAG.sub("", line)).strip() for line in block.splitlines()] for block in found]


def _lines(start: str) -> list[tuple[str, str]]:
    return [(name, line) for name in PAGES for block in _blocks(name) for line in block if line.startswith(start)]


def test_every_claude_code_marketplace_line_checks_out_only_the_plugin():
    want = f"{CLAUDE_ADD} --sparse {' '.join(_sparse())}"
    found = _lines(CLAUDE_ADD)

    assert len(found) >= 4, found
    assert [(name, line) for name, line in found if line != want] == []


def test_every_codex_marketplace_line_is_sparse_and_pinned_to_the_release():
    sparse = "".join(f" --sparse {path}" for path in _sparse())
    found = _lines(CODEX_ADD)

    def want(name: str) -> str:
        tag = "vVERSION" if name == RUNBOOK else f"v{_version()}"
        return f"{CODEX_ADD} --ref {tag}{sparse}"

    assert len(found) >= 6, found
    assert [(name, line) for name, line in found if line != want(name)] == []


def _stamped() -> dict[str, int]:
    """The pages the release step rewrites the Codex ref on, and how many times."""
    return {surface.path: surface.count for surface in release.SURFACES if surface.pattern == "--ref v{v}"}


def test_the_release_rewrites_every_codex_ref_a_page_prints():
    pinned = f"--ref v{_version()}"
    printed = {name: _doc(name).count(pinned) for name in PAGES if pinned in _doc(name)}

    assert printed and printed == _stamped()


def _codex_refreshes() -> list[tuple[str, list[str]]]:
    return [(name, block) for name in PAGES for block in _blocks(name)
            if CODEX_REMOVE in block or CODEX_UPGRADE in block]


def test_the_codex_refresh_moves_the_marketplace_to_the_new_tag_then_installs():
    refreshes = _codex_refreshes()

    assert len(refreshes) >= 4, refreshes
    for name, block in refreshes:
        steps = [line for line in block if line.startswith("codex plugin")]
        assert [step.split(" --ref")[0] for step in steps[:4]] == \
            [CODEX_REMOVE, CODEX_ADD, CODEX_INSTALL, CODEX_LIST], (name, steps)


def test_no_codex_refresh_repeats_the_install_an_upgrade_already_did():
    for name, block in _codex_refreshes():
        if CODEX_UPGRADE in block:
            assert CODEX_INSTALL not in block[block.index(CODEX_UPGRADE):], name


def test_the_codex_install_names_the_codex_its_lines_need():
    """0.130.0 answers `codex plugin add` with `unrecognized subcommand 'add'`, and
    0.136.0 answers the listing's `--json` with `unexpected argument`."""
    for name in ("README.md", "docs/upgrading.md", "docs/adoption.md", "docs/handbook.html"):
        text = " ".join(_doc(name).split())
        assert "Codex 0.131.0 or newer" in text, name
        assert "0.137.0" in text, name


@pytest.mark.parametrize("name", ["README.md", "docs/upgrading.md"])
def test_the_pages_say_when_an_installed_plugin_moves(name):
    text = " ".join(_doc(name).split())

    assert "The installed plugin moves only at a release" in text
    assert "A marketplace added at a tag stays there" in text
