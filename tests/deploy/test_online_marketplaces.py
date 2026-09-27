"""The README's marketplace lines against the real github.com, with the
network on: the weekly job and the smoke after a release.

Claude Code clones a marketplace with `git clone --depth 1` of the whole
repository, under its own 120-second clone timeout; Codex clones it too. The
README's lines pass `--sparse .claude-plugin plugin`, which fetches only what
the plugin needs. The cells run them as github.com's README prints them, from
the tree under test before the candidate's stamp: the stamped lines pin a tag
github.com does not hold until the release. Each cell installs the plugin and
records each clone's size on disk.

    lin-online-marketplaces   the README lines install the plugin at the clone's version from a
                              sparse clone; the same lines without --sparse clone the whole
                              repository, the size docs/upgrading.md gives for re-adding sparse
"""
from __future__ import annotations

import json
from pathlib import Path

import hang_guard

from kit import wheels
from kit.cells import cell
from test_claude_plugin import CLAUDE, harness_on_path, installed, page_lines, plain_repo, run_lines
from test_codex_plugin import codex_version

PACKET = "deploy-plugins"
SPARSE = (".claude-plugin", "plugin")
# A full clone of the repository's tip is 61 MB. It took 11 s on one run and
# ran into Claude Code's own 120 s clone timeout on another, so the lines
# without --sparse get 600 s here, not the kit's 120 s.
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


def unsparse(line: str) -> str:
    """The line with its --sparse paths left out, as the README printed it before 0.8.1."""
    kept, dropping = [], False
    for word in line.split():
        dropping = word == "--sparse" or (dropping and not word.startswith("--"))
        if not dropping:
            kept.append(word)
    return " ".join(kept)


def from_github(box, repo: Path, heading: str, sparse: bool) -> list:
    """The README's two lines as github.com shows them, or without --sparse."""
    add, install = page_lines(heading, base=wheels.SRC)
    return run_lines(box, [add if sparse else unsparse(add), install], cwd=repo)


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


@cell("lin-online-marketplaces", channel="GitHub marketplaces, README lines verbatim", harness="Claude Code, Codex",
      scenario="fresh: the README lines as github.com prints them clone only .claude-plugin and plugin and install "
               "the plugin at the clone's version", use_cases="plugin install", os="linux", image="core",
      cadence="weekly+published", online=True)
def test_readme_marketplace_lines_clone_only_the_plugin(box):
    found = both_harnesses(box, sparse=True)

    for result in found.values():
        assert result["directories"] == sorted(SPARSE)
        assert result["installed"] == result["clone_version"]
        assert result["bytes"] < PLUGIN_BUDGET


@cell("lin-online-marketplaces", channel="GitHub marketplaces, README lines without --sparse",
      harness="Claude Code, Codex",
      scenario="fresh: the lines without --sparse, as 0.8.0's README printed them, clone the whole repository",
      use_cases="plugin install", os="linux", image="core", cadence="weekly+published", online=True)
def test_lines_without_sparse_clone_the_whole_repository(box, monkeypatch):
    monkeypatch.setattr(hang_guard, "HANG_SECONDS", CLONE_SECONDS)
    found = both_harnesses(box, sparse=False)

    for result in found.values():
        assert result["installed"] == result["clone_version"]
        assert result["bytes"] > PLUGIN_BUDGET
