"""The plugin and the CLI out of step: the ways a user ends up with one ahead
of the other, or with a plugin that is not what the marketplace holds, and
what `crapkit doctor --plugin-root` tells them.

    lin-claude-plugin-ahead      the README update lines run before the CLI upgrade: the plugin
                                 is ahead, doctor names both repairs, the CLI one clears it
    lin-main-between-releases    main moves past a release with the version string unchanged:
                                 Codex reinstalls the new files, Claude Code keeps the old ones
    lin-marketplace-pinned-tag   a marketplace added at @v0.7.6 stays there through the update
                                 lines; re-adding it at the new tag is what moves it
    win-up-plugins-0.7.6         Claude Code and Codex upgrade their 0.7.6 copies on Windows while
                                 a session still holds a file of the old copy open
"""
from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

import pytest

from kit.cells import cell
from test_claude_plugin import (CLAUDE, cli_venv, doctor_plugin, github, harness_on_path, install_old_plugin,
                                installed, old_lines, page_lines, plain_repo, run_lines, upgrade_both, upgrade_cli)
from test_codex_plugin import codex_root, codex_version, gap_repairs, install_old, run_guide

PACKET = "deploy-plugins"
OLD = "0.7.6"
SKILL = Path("skills") / "crapkit" / "SKILL.md"


# --- the plugin ahead of the CLI ---------------------------------------------------------

@cell("lin-claude-plugin-ahead", channel="Claude marketplace", harness="Claude Code",
      scenario="drift: CLI 0.7.6, the README update lines pull the candidate plugin; doctor gap; each repair it names",
      use_cases="plugin/CLI drift", os="linux", image="core", cadence="nightly")
def test_plugin_ahead_of_the_cli(box, candidate):
    repo = plain_repo(box)
    mirror = install_old_plugin(box, OLD, repo)
    mirror.publish(candidate.staged, candidate.version)
    steps = run_lines(box, page_lines(CLAUDE, index=1), cwd=repo, expect=None)
    gap = steps[-1]
    repairs = gap_repairs(gap.stdout)
    outcomes = [(box.script(command, cwd=repo).exit, doctor_plugin(box, cwd=repo).exit) for command in repairs]

    assert installed(box)["version"] == candidate.version
    assert gap.exit == 1 and f"is version {candidate.version}, and the crapkit its hooks spawn" in gap.stdout
    assert repairs == ["claude plugin install crapkit@crapkit", "pip install -U crapkit"]
    assert outcomes == [(0, 1), (0, 0)]


# --- main between releases ---------------------------------------------------------------

def between_releases(box, mirror, candidate) -> Path:
    """main one commit past the candidate release, same version string, one skill
    line added; the tag stays on the release. Returns the new tree."""
    tree = box.root / "tree-main"
    shutil.copytree(candidate.staged, tree)
    with (tree / "plugin" / SKILL).open("a", encoding="utf-8", newline="\n") as skill:
        skill.write("\nA line main gained after the release.\n")
    release = mirror.head(f"v{candidate.version}")
    mirror.publish(tree, candidate.version)
    mirror.git("tag", "-f", f"v{candidate.version}", release)
    return tree


def same_skill(tree: Path, root: Path) -> bool:
    return filecmp.cmp(tree / "plugin" / SKILL, root / SKILL, shallow=False)


def both_plugins_at_candidate(box, candidate, repo: Path):
    cli_venv(box)
    mirror = github(box, candidate)
    harness_on_path(box)
    run_lines(box, page_lines(CLAUDE), cwd=repo)
    run_lines(box, page_lines("Codex"), cwd=repo)
    return mirror


@cell("lin-main-between-releases", channel="marketplace at a main commit", harness="Codex",
      scenario="drift: same version, different contents; Codex's refresh lines install main's files; doctor 0",
      use_cases="plugin drift", os="linux", image="core", cadence="nightly")
def test_codex_refresh_installs_main_between_releases(box, candidate):
    repo = plain_repo(box)
    tree = between_releases(box, both_plugins_at_candidate(box, candidate, repo), candidate)
    run_lines(box, page_lines("Codex", index=1), cwd=repo)
    root = codex_root(box, codex_version(box))

    assert same_skill(tree, root)
    assert doctor_plugin(box, str(root)).exit == 0


@cell("lin-main-between-releases", channel="marketplace at a main commit", harness="Claude Code",
      scenario="drift: same version, different contents; after the README update lines Claude Code runs main's "
      "files, or doctor --plugin-root says it does not", use_cases="plugin drift", os="linux", image="core",
      cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-plugins-7: main past a release keeps the release's version "
                   "string, so `claude plugin update` answers 'already at the latest version', the installed copy keeps "
                   "the release's files, and doctor --plugin-root exits 0")
def test_claude_update_between_releases_is_seen(box, candidate):
    repo = plain_repo(box)
    tree = between_releases(box, both_plugins_at_candidate(box, candidate, repo), candidate)
    steps = run_lines(box, page_lines(CLAUDE, index=1), cwd=repo, expect=None)
    box.transcript.note(f"`claude plugin update` said: {steps[1].stdout.strip()}")

    assert same_skill(tree, Path(installed(box)["installPath"])) or steps[-1].exit == 1


# --- a marketplace pinned to a tag ----------------------------------------------------------

def pinned(line: str, version: str) -> str:
    """A README marketplace line with its source pinned to the release tag:
    `owner/repo@vX` for Claude Code's shorthand, `--ref vX` after Codex's URL."""
    return f"{line} --ref v{version}" if "://" in line else f"{line}@v{version}"


def claude_pinned_at(box, version: str, cwd: Path) -> None:
    add, install = page_lines(CLAUDE)
    run_lines(box, [pinned(add, version), install], cwd=cwd)


@cell("lin-marketplace-pinned-tag", channel="marketplace add @vX", harness="Claude Code",
      scenario="upgrade: the README update lines leave a marketplace pinned at @v0.7.6 there; re-adding it at the "
      "new tag moves it", use_cases="plugin upgrade", os="linux", image="core", cadence="nightly")
def test_claude_marketplace_pinned_to_a_tag(box, candidate):
    repo = plain_repo(box)
    cli_venv(box, spec=f"crapkit=={OLD}")
    mirror = github(box, at=OLD)
    harness_on_path(box)
    claude_pinned_at(box, OLD, repo)
    mirror.publish(candidate.staged, candidate.version)
    upgrade_cli(box)
    stuck = run_lines(box, page_lines(CLAUDE, index=1), cwd=repo, expect=None)
    stuck_version = installed(box)["version"]
    box.script("claude plugin marketplace remove crapkit", cwd=repo, expect=0)
    claude_pinned_at(box, candidate.version, repo)

    assert stuck_version == OLD and stuck[-1].exit == 1
    assert installed(box)["version"] == candidate.version
    assert doctor_plugin(box, cwd=repo).exit == 0


@cell("lin-marketplace-pinned-tag", channel="marketplace add @vX", harness="Codex",
      scenario="upgrade: the docs refresh lines leave a marketplace pinned at @v0.7.6 there; re-adding it at the "
      "new tag moves it", use_cases="plugin upgrade", os="linux", image="core", cadence="nightly")
def test_codex_marketplace_pinned_to_a_tag(box, candidate):
    repo = plain_repo(box)
    cli_venv(box, spec=f"crapkit=={OLD}")
    mirror = github(box, at=OLD)
    harness_on_path(box)
    add, plugin_add = page_lines("Codex")
    run_lines(box, [pinned(add, OLD), plugin_add], cwd=repo)
    mirror.publish(candidate.staged, candidate.version)
    upgrade_cli(box)
    run_guide(box, repo, expect=None)
    stuck_version = codex_version(box)
    box.script("codex plugin marketplace remove crapkit", cwd=repo, expect=0)
    run_lines(box, [pinned(add, candidate.version), plugin_add], cwd=repo)
    root = codex_root(box, codex_version(box))

    assert stuck_version == OLD
    assert root.name == candidate.version
    assert doctor_plugin(box, str(root), cwd=repo).exit == 0


# --- Windows, a session holding the old copy ------------------------------------------------

def held(root: Path):
    """A handle on a file of an installed copy, as a running session holds one."""
    return (root / SKILL).open("rb")


def leftovers(*roots: Path) -> list[Path]:
    return [root for root in roots if root.exists()]


@cell("win-up-plugins-0.7.6", channel="Claude + Codex", harness="Claude Code, Codex",
      scenario="upgrade: CLI first, then each harness's documented update lines while a session holds a file of "
      "the 0.7.6 copy; both land on the candidate", use_cases="plugin upgrade", os="windows", image=None,
      cadence="nightly")
def test_windows_plugin_upgrades_with_the_old_copy_open(box, candidate):
    repo = plain_repo(box)
    mirror = install_old(box, OLD, repo)
    run_lines(box, old_lines(box, mirror, OLD, "claude plugin install"), cwd=repo)
    claude_old = Path(installed(box)["installPath"])
    with held(claude_old), held(codex_root(box, OLD)):
        upgrade_both(box, mirror, candidate, repo)
        codex_steps = run_guide(box, repo, expect=None)
    box.transcript.note(f"left behind: {leftovers(claude_old, codex_root(box, OLD))}")

    assert installed(box)["version"] == candidate.version
    assert [step.exit for step in codex_steps] == [0, 0, 0, 0]
    assert codex_version(box) == candidate.version
