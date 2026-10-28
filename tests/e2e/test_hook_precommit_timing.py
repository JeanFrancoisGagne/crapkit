"""The shared hook timing test: what `git commit` waits for while hook-precommit runs.

Two shapes on tests/fixtures/mini_repo, each timed over 30 spawned runs:

- clean: a staged edit to `plain`, under the ceiling. No mark is read and no
  base blob is.
- straddling: `tangled` (ccn 8), added to pylib/mod.py, carries the mark
  60.0000, and a staged branch takes it to ccn 9. Its range, 9 to 90, straddles
  the mark and its ccn rose, so the hook reads the marks file and the base
  blob, and warns.

Two tiers, built like claude-hook's in test_claude_hook_e2e.py. The hard one is
a wall-clock ceiling on each shape's median that CI enforces: it catches a hook
that hangs, opens the store or spawns a git process per function. A box whose
bare interpreter starts too slowly is busy with something else and is excused
by that file's rule; CI never is.

The budget tier holds the hook to 0.8.1's: CRAPKIT_TIMING_REFERENCE_PYTHON
names an interpreter with crapkit 0.8.1 installed, and each shape's median may
sit at most 50 ms above that interpreter's median on the same shape. Slot close
runs it on an idle Desktop B, and the release candidate on the built wheel.
"""
from __future__ import annotations

import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import child_env, git_commit_all, git_init_repo
from test_claude_hook_e2e import _excuse_a_saturated_box

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_repo"
RUNS = 30
# The median of 30 runs, per shape. The straddling shape adds one marks-file
# read and one `git cat-file --batch` to the clean one.
HARD_CEILING_MS = 2500.0
BUDGET_ABOVE_081_MS = 50.0
REFERENCE = "CRAPKIT_TIMING_REFERENCE_PYTHON"
TANGLED = "".join(f"    if n > {i}:\n        n += 1\n" for i in range(1, 8))
ONE_MORE_BRANCH = ("    if n > 7:\n", "    if n > 8:\n        n += 1\n    if n > 7:\n")
PLAIN_EDIT = ("  if (x > 10) {\n", "  if (x > 11) {\n")
WARN_HEAD = "rose in ccn and may pass their ratchet mark"


def _edit(repo: Path, rel: str, old: str, new: str) -> None:
    path = repo / rel
    text = path.read_bytes().decode("utf-8")
    assert old in text, rel
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def _shape(tmp_path: Path, name: str) -> Path:
    """mini_repo committed with `tangled` added and marked at 60, then the
    shape's edit staged."""
    repo = tmp_path / name
    shutil.copytree(MINI, repo)
    (repo / ".gitignore").write_bytes(b".crapkit/\n__pycache__/\n")
    module = repo / "pylib" / "mod.py"
    module.write_bytes(module.read_bytes() + f"\n\ndef tangled(n):\n{TANGLED}    return n\n".encode("utf-8"))
    (repo / "crapkit-ratchet.tsv").write_bytes(b"path\tlong_name\tcrap\npylib/mod.py\ttangled( n )\t60.0000\n")
    git_init_repo(repo)
    git_commit_all(repo, "init")
    if name == "straddling":
        _edit(repo, "pylib/mod.py", *ONE_MORE_BRANCH)
    else:
        _edit(repo, "src/app.ts", *PLAIN_EDIT)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    return repo


def _environment() -> dict:
    """The hook as `git commit` starts it: no override reason, and none of the
    suite's own coverage hooks, which would time coverage instead of crapkit."""
    dropped = [key for key in os.environ if key.startswith(("COVERAGE_", "COV_CORE_"))]
    return child_env({key: None for key in (*dropped, "CRAPKIT_OVERRIDE_REASON")})


def _run(python: str, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([python, "-m", "crapkit", "hook-precommit"], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=_environment(), timeout=300)


def _median_ms(python: str, repo: Path) -> float:
    """The median of RUNS hook runs after one warming run."""
    _run(python, repo)
    runs = []
    for _ in range(RUNS):
        start = time.perf_counter()
        _run(python, repo)
        runs.append((time.perf_counter() - start) * 1000)
    return statistics.median(runs)


SHAPES = ("clean", "straddling")


@pytest.mark.parametrize("name", SHAPES)
def test_each_shape_is_the_commit_it_names(tmp_path: Path, name: str):
    """The clean shape passes in silence; the straddling one passes on the warn."""
    done = _run(sys.executable, _shape(tmp_path, name))

    assert done.returncode == 0, done.stdout + done.stderr
    assert (WARN_HEAD in done.stderr) == (name == "straddling"), done.stderr
    assert done.stderr == "" or name == "straddling", done.stderr


@pytest.mark.parametrize("name", SHAPES)
def test_each_shapes_median_stays_inside_the_ci_ceiling(tmp_path: Path, name: str):
    _excuse_a_saturated_box()

    assert _median_ms(sys.executable, _shape(tmp_path, name)) < HARD_CEILING_MS


@pytest.mark.skipif(not os.environ.get(REFERENCE), reason=f"the 0.8.1 budget: set {REFERENCE}")
@pytest.mark.parametrize("name", SHAPES)
def test_each_shapes_median_sits_within_50_ms_of_081s(tmp_path: Path, name: str):
    repo = _shape(tmp_path, name)
    reference = _median_ms(os.environ[REFERENCE], repo)

    assert _median_ms(sys.executable, repo) - reference <= BUDGET_ABOVE_081_MS, reference
