"""A tree with tracked files deleted names them in one order under every hash seed.

inventory, coverage and verify skip a tracked file that is gone from the working
tree and print one stderr line for it. Those lines came out of a set difference,
and Python salts str hashing per process, so the same tree printed them in
another order on each run: 7 orders over PYTHONHASHSEED 0-7 with four files
deleted. A golden test or a diff of stderr read that as a change. They now
print in path order, the order the scope's file list already has.

The seed is read only as the interpreter starts, so each run is a process of
its own.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

NAMES = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
# Out of path order on purpose: the deletions are made in this order.
DELETED = ["hotel", "bravo", "foxtrot", "delta", "alpha"]
TOML = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "pkg"\npaths = ["pkg"]\nlanguages = ["python"]\n'
SKIPPED = "crapkit: tracked file missing from working tree, skipped: "
SEEDS = [str(seed) for seed in range(8)]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    """Eight tracked one-function files under one Python scope."""
    repo = tmp_path_factory.mktemp("missing") / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8", newline="\n")
    for name in NAMES:
        (repo / "pkg" / f"{name}.py").write_text(f"def {name}(x):\n    return x\n",
                                                 encoding="utf-8", newline="\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return repo


def _repo(template: Path, tmp_path: Path, deleted: int) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(template, repo)
    for name in DELETED[:deleted]:
        (repo / "pkg" / f"{name}.py").unlink()
    return repo


def _skipped(repo: Path, seed: str) -> list[str]:
    """The paths inventory names as skipped, in the order it printed them."""
    done = subprocess.run([sys.executable, "-m", "crapkit", "inventory", "--repo", str(repo)],
                          env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True,
                          text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    return [line.removeprefix(SKIPPED) for line in done.stderr.splitlines()
            if line.startswith(SKIPPED)]


@pytest.mark.parametrize("deleted", [1, 4, 5])
def test_the_skipped_lines_print_in_path_order_under_every_hash_seed(template, tmp_path, deleted):
    repo = _repo(template, tmp_path, deleted)
    expected = [f"pkg/{name}.py" for name in sorted(DELETED[:deleted])]

    printed = {seed: _skipped(repo, seed) for seed in SEEDS}

    assert printed == {seed: expected for seed in SEEDS}
