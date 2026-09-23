"""The churn commit table through the CLI, on a real repo with real git.

A new commit misses the per-file map, and the map is rebuilt from the stored
commit table plus `git log stored..HEAD`. Two things have to hold: the answer is
byte-identical to a cold rebuild, and no window walk happened. The second is
proved by a census of the git commands the run spawned (GIT_TRACE2_EVENT).
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

CRAPKIT = Path(".crapkit")
TABLE = CRAPKIT / "churn-commits-v1.json"
CHURN_FILES = ("churn-cache-v2.json", "churn-commits-v1.json")

APP_PY = """def pick(kind):
    if kind == "a":
        return 1
    if kind == "b":
        return 2
    return 0
"""

TOML = (
    '[crapkit]\ntarget = 6\nworklist_floor = 1\nchurn_window_months = 12\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
)

run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")
WORKLIST = ("worklist", "--json")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def commit(repo: Path, message: str, *extra: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, *extra)


def _log_walks(trace_dir: Path) -> list[list[str]]:
    """Every `git log` a traced run spawned, past git's own -c flags."""
    walks = []
    for path in sorted(trace_dir.glob("*")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            event = json.loads(line)
            argv = event.get("argv", [])
            if event.get("event") == "start" and "log" in argv:
                walks.append(argv)
    return walks


def traced(repo: Path, tmp_path: Path, tag: str, *args: str):
    trace = tmp_path / f"trace-{tag}"
    trace.mkdir()
    res = run_cli(repo, *args, env_extra={"GIT_TRACE2_EVENT": str(trace)})
    return res, _log_walks(trace)


def window_walks(walks):
    return [argv for argv in walks if any(a.startswith("--since") for a in argv)]


def range_walks(walks):
    return [argv for argv in walks if any(".." in a for a in argv)]


def rebuilt(repo: Path):
    """The same question with every churn cache gone: the cold answer."""
    for name in CHURN_FILES:
        (repo / CRAPKIT / name).unlink(missing_ok=True)
    return run_cli(repo, *WORKLIST)


@pytest.fixture()
def churned_repo(tmp_path: Path) -> Path:
    """src/app.py lands in three commits, and the map and table are laid down."""
    repo = tmp_path / "churned"
    write(repo / "crapkit.toml", TOML)
    write(repo / ".gitignore", ".crapkit/\n__pycache__/\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    for i in range(3):
        write(repo / "src" / "app.py", APP_PY + f"\nBUILD = {i}\n")
        commit(repo, f"edit {i}")
    assert run_cli(repo, "inventory").returncode == 0
    laid = run_cli(repo, *WORKLIST)
    assert laid.returncode == 0, laid.stdout + laid.stderr
    assert (repo / TABLE).is_file(), "the first map read keeps the window's commits"
    return repo


def test_a_new_commit_walks_only_itself_and_answers_what_a_rebuild_does(churned_repo, tmp_path):
    write(churned_repo / "src" / "app.py", APP_PY + "\nBUILD = 9\n")
    commit(churned_repo, "move head")

    moved, walks = traced(churned_repo, tmp_path, "moved", *WORKLIST)
    assert moved.returncode == 0, moved.stdout + moved.stderr
    assert window_walks(walks) == [], "one new commit must not cost the window"
    assert len(range_walks(walks)) == 1
    assert json.loads(moved.stdout)["active"][0]["commits"] == 4

    assert rebuilt(churned_repo).stdout == moved.stdout, "carried and cold answer byte for byte"


def test_an_amended_head_rebuilds_the_window(churned_repo, tmp_path):
    """The stored HEAD is gone from history: folding onto it would keep a commit
    the branch no longer has."""
    write(churned_repo / "src" / "app.py", APP_PY + "\nBUILD = 7\n")
    commit(churned_repo, "rewritten", "--amend")

    amended, walks = traced(churned_repo, tmp_path, "amended", *WORKLIST)
    assert amended.returncode == 0, amended.stdout + amended.stderr
    assert window_walks(walks), "a rewritten history is walked again in full"
    assert range_walks(walks) == []
    assert json.loads(amended.stdout)["active"][0]["commits"] == 3

    assert rebuilt(churned_repo).stdout == amended.stdout
