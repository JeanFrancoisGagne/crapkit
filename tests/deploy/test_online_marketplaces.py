"""The README's marketplace lines against the real github.com, with the
network on: the weekly job and the smoke after a release.

Claude Code clones a marketplace with `git clone --depth 1` of the whole
repository, under its own 120-second clone timeout; Codex clones it too. Both
accept `--sparse <paths>`, which fetches only what the plugin needs. The cell
adds the marketplace both ways in each harness, installs the plugin, and
records each clone's size on disk.

    lin-online-marketplaces   sparse lines install the plugin at the clone's version; the README
                              lines as printed clone the whole repository
"""
from __future__ import annotations

import json
from pathlib import Path

import hang_guard
import pytest

from kit.cells import cell
from test_claude_plugin import CLAUDE, harness_on_path, installed, page_lines, plain_repo, run_lines
from test_codex_plugin import codex_version

PACKET = "deploy-plugins"
SPARSE = (".claude-plugin", "plugin")
# A full clone of the repository's tip is 61 MB. It took 11 s on one run and
# ran into Claude Code's own 120 s clone timeout on another, so the README
# lines get 600 s here, not the kit's 120 s.
CLONE_SECONDS = 600
PLUGIN_BUDGET = 10 * 1024 * 1024


def disk_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def manifest_version(clone: Path) -> str:
    return json.loads((clone / "plugin" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def directories(clone: Path) -> list[str]:
    """The clone's top-level directories. A sparse checkout in cone mode keeps
    every top-level file, so the directories are what it narrowed."""
    return sorted(path.name for path in clone.iterdir() if path.is_dir() and path.name != ".git")


def claude_clone(box) -> Path:
    known = json.loads((Path(box.env["CLAUDE_CONFIG_DIR"]) / "plugins" / "known_marketplaces.json").read_text(
        encoding="utf-8"))
    return Path(known["crapkit"]["installLocation"])


def codex_clone(box) -> Path:
    return Path(box.env["CODEX_HOME"]) / ".tmp" / "marketplaces" / "crapkit"


def sparse_line(line: str) -> str:
    """A marketplace line with the plugin's two directories as its sparse paths:
    Claude Code takes them after one flag, Codex one flag each."""
    if line.startswith("claude "):
        return f"{line} --sparse {' '.join(SPARSE)}"
    return line + "".join(f" --sparse {path}" for path in SPARSE)


def from_github(box, repo: Path, heading: str, sparse: bool) -> list:
    add, install = page_lines(heading)
    return run_lines(box, [sparse_line(add) if sparse else add, install], cwd=repo)


def seen(clone: Path, version: str | None, steps: list) -> dict:
    return {"bytes": disk_bytes(clone), "directories": directories(clone), "clone_version": manifest_version(clone),
            "installed": version, "seconds": [step.seconds for step in steps]}


def both_harnesses(box, sparse: bool) -> dict[str, dict]:
    """Claude Code and Codex, each given the marketplace and the plugin from github.com."""
    repo = plain_repo(box)
    harness_on_path(box)
    claude = from_github(box, repo, CLAUDE, sparse)
    codex = from_github(box, repo, "Codex", sparse)
    found = {"claude": seen(claude_clone(box), installed(box).get("version"), claude),
             "codex": seen(codex_clone(box), codex_version(box), codex)}
    box.transcript.attach("clones", found)
    return found


@cell("lin-online-marketplaces", channel="GitHub marketplaces, --sparse .claude-plugin plugin",
      harness="Claude Code, Codex",
      scenario="fresh: the README lines with --sparse against github.com install the plugin at the clone's version",
      use_cases="plugin install", os="linux", image="core", cadence="weekly+published", online=True)
def test_sparse_marketplace_lines_install_the_plugin(box):
    found = both_harnesses(box, sparse=True)

    for result in found.values():
        assert result["directories"] == sorted(SPARSE)
        assert result["installed"] == result["clone_version"]
        assert result["bytes"] < PLUGIN_BUDGET


@cell("lin-online-marketplaces", channel="GitHub marketplaces, README lines verbatim", harness="Claude Code, Codex",
      scenario="fresh: the README lines as printed install the plugin without cloning the whole repository",
      use_cases="plugin install", os="linux", image="core", cadence="weekly+published", online=True)
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-plugins-10: the README's marketplace lines clone the whole "
                   "repository tip, 61 MB for Claude Code and 69 MB for Codex, against 0.8 MB and 1.9 MB with --sparse "
                   ".claude-plugin plugin; one run's Claude Code add hit its 120 s clone timeout and failed after 110 s")
def test_readme_marketplace_lines_clone_only_the_plugin(box, monkeypatch):
    monkeypatch.setattr(hang_guard, "HANG_SECONDS", CLONE_SECONDS)
    found = both_harnesses(box, sparse=False)

    for result in found.values():
        assert result["installed"] == result["clone_version"]
        assert result["bytes"] < PLUGIN_BUDGET
