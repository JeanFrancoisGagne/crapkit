"""The Go recipe's measured time: `pip install crapkit` to a covered worklist.

Run once and by hand, not in the suite: it needs the
network, Go on PATH and a few minutes. Set CRAPKIT_GO_RECIPE=1, and
CRAPKIT_GO_RECIPE_INSTALL to what pip installs (default `crapkit`; a wheel
path or a checkout before the release). It installs crapkit into a fresh
venv, clones github.com/google/go-cmp at v0.7.0, runs `crapkit init`,
uncomments the go stanza, deletes coverage_optional, runs `crapkit coverage`
and `crapkit worklist`, and prints the wall time, the machine and the date
docs/lanes.md's Go recipe quotes.
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

import pytest

REPO = "https://github.com/google/go-cmp"
TAG = "v0.7.0"

pytestmark = pytest.mark.skipif(
    os.environ.get("CRAPKIT_GO_RECIPE") != "1" or shutil.which("go") is None,
    reason="run by hand: set CRAPKIT_GO_RECIPE=1 with Go and the network")


def _run(*args: str, cwd: Path) -> str:
    done = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8", timeout=900)
    return done.stdout


def _uncommented(text: str) -> str:
    """The go stanza uncommented and coverage_optional deleted."""
    lines, inside = [], False
    for line in text.split("\n"):
        inside = inside or line == "# [[lane]]"
        if inside and not line:
            inside = False
        if line == "coverage_optional = true":
            continue
        lines.append(line[2:] if inside else line)
    return "\n".join(lines)


def test_pip_install_to_a_covered_worklist_on_a_public_go_module(tmp_path):
    started = time.monotonic()
    env = tmp_path / "venv"
    venv.create(env, with_pip=True)
    bin_dir = env / ("Scripts" if os.name == "nt" else "bin")
    install = os.environ.get("CRAPKIT_GO_RECIPE_INSTALL", "crapkit")
    _run(str(bin_dir / "python"), "-m", "pip", "install", "-q", install, cwd=tmp_path)
    crapkit = str(bin_dir / "crapkit")
    repo = tmp_path / "go-cmp"
    _run("git", "clone", "-q", "--depth", "1", "--branch", TAG, REPO, str(repo), cwd=tmp_path)

    _run(crapkit, "init", cwd=repo)
    config = repo / "crapkit.toml"
    config.write_text(_uncommented(config.read_text(encoding="utf-8")), encoding="utf-8",
                      newline="\n")
    _run(crapkit, "coverage", cwd=repo)
    worklist = json.loads(_run(crapkit, "worklist", "--json", cwd=repo))
    elapsed = time.monotonic() - started

    rows = [row for value in worklist.values() if isinstance(value, list) for row in value
            if isinstance(row, dict)]
    assert any(row.get("cov") is not None for row in rows), worklist
    print(f"\nGo recipe: {elapsed:.0f} s from pip install to a covered worklist, "
          f"{REPO} {TAG}, {platform.platform()} {platform.machine()}, Python "
          f"{sys.version.split()[0]}, {datetime.date.today().isoformat()}")
