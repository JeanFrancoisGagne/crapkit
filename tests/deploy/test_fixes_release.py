"""The release gate on the candidate a deploy run built.

candidate.py stamps the tree through release.py's SURFACES table, so the
stamped tree is what `release.py check` would see at release time. check has
to find the candidate's version on every surface, the Codex manifest among
them, and still refuse: no green release-cadence run of deploy.yml exists at
that commit, and gh cannot say otherwise from a sandbox.
"""
from __future__ import annotations

import json
import shutil

from kit.cells import cell

PACKET = "deploy-fixes"
SURFACE_FILES = ("pyproject.toml", "src/crapkit/__init__.py", "README.md",
                 "plugin/.claude-plugin/plugin.json", "plugin/.codex-plugin/plugin.json", "server.json")


def next_patch(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _stamped_repo(box, candidate):
    """The stamped candidate tree as the one commit of a repository."""
    tree = box.root / "release-tree"
    shutil.copytree(candidate.staged, tree)
    box.run(["git", "init", "-q", "-b", "main"], cwd=tree, expect=0)
    box.run(["git", "add", "-A"], cwd=tree, expect=0)
    box.run(["git", "commit", "-q", "-m", f"candidate {candidate.version}"], cwd=tree,
            env=box.commit_env(), expect=0)
    return tree


def _check_the_stamped_tree(box, candidate):
    tree = _stamped_repo(box, candidate)
    step = box.run([box.toolchain.python("3.12"), "tools/release/release.py", "check",
                    next_patch(candidate.version), "--repo", str(tree)], cwd=tree, expect=1)
    lines = step.stdout.splitlines()
    codex = json.loads((tree / "plugin" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert (codex["version"], codex["hooks"]) == (candidate.version, {})
    assert [line for line in lines if line.startswith(SURFACE_FILES)] == [], "every surface agrees"
    (gate,) = [line for line in lines if line.startswith("deploy gate: ")]
    assert "gh" in gate, gate


@cell("lin-release-check-candidate", channel="tools/release/release.py on the stamped candidate",
      harness="none", scenario="release gate: check finds the candidate version on every surface, "
      "the Codex manifest included, and refuses with no green release-cadence deploy run",
      use_cases="release gate", os="linux", image="core", cadence="push", real_cli=False)
def test_release_check_sees_one_version_and_waits_for_the_deploy_suite(box, candidate):
    _check_the_stamped_tree(box, candidate)


@cell("win-release-check-candidate", channel="tools/release/release.py on the stamped candidate",
      harness="none", scenario="release gate: the Linux cell on Windows, where releases are cut",
      use_cases="release gate", os="windows", image=None, cadence="push", real_cli=False)
def test_on_windows_release_check_sees_one_version_and_waits_for_the_deploy_suite(box, candidate):
    _check_the_stamped_tree(box, candidate)
