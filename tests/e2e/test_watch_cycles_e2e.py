"""The watch loop driven to a known end.

`--cycles N` is the seam that makes the loop testable at all. The unbounded
default only ever ends in a signal, and a watcher killed with SIGTERM proves
nothing about its exit code and writes no coverage on the way out.

Three runs, no wall-clock waits anywhere: a real subprocess that stops on its
own, an in-process run whose only stub is the clock (the file is touched during
the poll's own sleep, and the rescore that fires is a real child process), and
an interrupt during that same sleep.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from crapkit.cli import build_parser, cmd_watch

APP = "def branchy(a, b):\n    if a > 0:\n        b += 1\n    return a + b\n"

# Stand-in for the py lane: a coverage.py-format artifact for src/app.py with
# one of branchy's two branches taken, so the scored run is hand-computable.
MAKE_COV = '''import json

fn = {"start_line": 1, "executed_lines": [1, 2, 3, 4], "missing_lines": [],
      "summary": {"covered_lines": 4, "num_statements": 4,
                  "num_branches": 2, "covered_branches": 1}}
report = {"meta": {"branch_coverage": True},
          "files": {"src/app.py": {"functions": {"branchy": fn}, "missing_lines": []}}}
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump(report, fh, sort_keys=True)
'''

TOML = (
    '[crapkit]\ntarget = 6\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
    '[[lane]]\nname = "py"\ncommand = "python make_cov.py"\nartifact = "cov.json"\n'
    'parser = "coveragepy"\nscopes = ["src"]\nfull_suite = false\n'
)


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo,
                          capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace", env=dict(os.environ))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    write(tmp_path / "src" / "app.py", APP)
    write(tmp_path / "make_cov.py", MAKE_COV)
    write(tmp_path / "crapkit.toml", TOML)
    write(tmp_path / ".gitignore", ".crapkit/\ncov.json\n__pycache__/\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], cwd=tmp_path, check=True,
                   capture_output=True)
    return tmp_path


@pytest.fixture()
def scored_repo(repo: Path) -> Path:
    """rescore overlays a SCORED run, so the watcher needs one to have something
    to hand its child."""
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return repo


def watch_args(repo: Path, *extra: str):
    return build_parser().parse_args(["watch", "--repo", str(repo), "--interval", "0", *extra])


def test_a_bounded_watch_polls_and_exits_on_its_own(repo: Path):
    res = run_cli(repo, "watch", "--interval", "0", "--cycles", "2")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "2 poll(s) then stop" in res.stdout
    assert "--- changed" not in res.stdout, "nothing moved, so nothing was rescored"


def test_a_file_touched_during_the_poll_is_rescored_before_the_run_ends(
        scored_repo: Path, monkeypatch, capfd):
    app = scored_repo / "src" / "app.py"
    # a fixed offset off the file's own mtime: changed_paths only asks whether
    # the two snapshots differ, so nothing here depends on the wall clock
    later = app.stat().st_mtime + 60

    def touch_while_the_watcher_waits(seconds: float) -> None:
        os.utime(app, (later, later))

    monkeypatch.setattr(time, "sleep", touch_while_the_watcher_waits)

    assert cmd_watch(watch_args(scored_repo, "--cycles", "1")) == 0

    out = capfd.readouterr().out
    assert "--- changed: src/app.py" in out
    assert "branchy" in out, "the child rescore ran and its table reached the watcher's output"


def test_an_untouched_repo_finishes_its_cycles_without_rescoring(scored_repo: Path, capfd):
    assert cmd_watch(watch_args(scored_repo, "--cycles", "3")) == 0

    assert "--- changed" not in capfd.readouterr().out


def test_ctrl_c_ends_an_unbounded_watch_with_a_zero_exit(repo: Path, monkeypatch, capfd):
    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", interrupt)

    assert cmd_watch(watch_args(repo)) == 0, "an operator stopping the watcher is not a failure"
    assert "ctrl-c to stop" in capfd.readouterr().out, \
        "no --cycles: the banner promises the loop that is actually running"
