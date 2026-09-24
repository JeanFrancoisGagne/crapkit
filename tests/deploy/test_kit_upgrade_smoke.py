"""The kit carries an upgrade end to end with no network: a repo adopted
under crapkit 0.7.6 from the wheelhouse, then upgraded to the candidate.

This is the kit's own proof, not the upgrade cells (lin-up-pip-0.7.6 and its
siblings follow docs/upgrading.md step by step). It holds the pieces those
cells stand on: an old release and the candidate side by side in find-links,
a pip upgrade that picks the candidate, and a store 0.7.6 wrote that the
candidate reads, reseeds and verifies.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from kit import repos

pytestmark = pytest.mark.kit

WINDOWS = os.name == "nt"
OLD = "0.7.6"
# docs/lanes.md#containers: the lane key that lets a coverage.py lane run in a container.
CONTAINER_KEY = "container_ok = true"


def in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1"


def allow_container_lane(repo: Path) -> None:
    config = repo / "crapkit.toml"
    text = config.read_text(encoding="utf-8")
    config.write_text(text.replace("[[lane]]\n", f"[[lane]]\n{CONTAINER_KEY}\n", 1), encoding="utf-8")


def crapkit(box, repo: Path, *args: str, expect: int | None = 0):
    return box.run(["crapkit", *args], cwd=repo, expect=expect)


def commit(box, repo: Path, message: str) -> None:
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", message], cwd=repo, env=box.commit_env(), expect=0)


def adopt(box, repo: Path) -> None:
    crapkit(box, repo, "init")
    if in_container():
        allow_container_lane(repo)
    crapkit(box, repo, "coverage")
    crapkit(box, repo, "ratchet", "seed")
    commit(box, repo, "adopt crapkit")


def test_a_repo_adopted_under_0_7_6_upgrades_to_the_candidate_offline(box, templates, candidate):
    repo = repos.checkout(box, "py-pytest", cache=templates)
    venv = box.root / "venv"
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(venv)], expect=0)
    box.prepend_path(venv / ("Scripts" if WINDOWS else "bin"))
    box.run(["python", "-m", "pip", "install", "-q", f"crapkit[py]=={OLD}"], expect=0)
    assert OLD in crapkit(box, repo, "--version").stdout
    adopt(box, repo)

    box.run(["python", "-m", "pip", "install", "-q", "--upgrade", "crapkit"], expect=0)
    assert candidate.version in crapkit(box, repo, "--version").stdout
    for step in (["coverage"], ["ratchet", "prune"], ["ratchet", "seed"]):
        crapkit(box, repo, *step)
    commit(box, repo, "reseed under the candidate")
    verdict = crapkit(box, repo, "verify")

    assert "verify OK" in verdict.stdout + verdict.stderr
