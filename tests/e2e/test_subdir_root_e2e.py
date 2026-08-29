"""Churn when the crapkit root is a subdirectory of the git working tree.

The field shape: a project living at `<repo>/app` (monorepo member, or an
agent-managed linked worktree whose checkout nests the project one level down).
Scored rows are root-relative because `git ls-files` answers relative to its
cwd, but `git log --name-only` answers relative to the repo top — `app/src/x.py`
against rows saying `src/x.py`. Every lookup missed, every file read as
zero-churn, and worklist filed the whole corpus under dormant.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MAKE_COV = ('import json\n'
            'json.dump({"meta": {"branch_coverage": True}, "files": {}},'
            ' open("cov.json", "w"))\n')

CONFIG = """[crapkit]
target = 6
worklist_floor = 5

[[scope]]
name = "core"
paths = ["core"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["core"]
full_suite = false
"""


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args],
                          cwd=repo, capture_output=True, text=True, timeout=180,
                          env=dict(os.environ))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def _source(ifs: int) -> str:
    body = "".join(f"    if a > {i}:\n        r += {i}\n" for i in range(1, ifs + 1))
    return f"def hot(a, b):\n    r = 0\n{body}    return r\n"


def _project(app: Path) -> None:
    (app / "core").mkdir(parents=True)
    (app / "core" / "hot.py").write_text(_source(5), encoding="utf-8")
    (app / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (app / "crapkit.toml").write_text(CONFIG, encoding="utf-8")


@pytest.fixture()
def nested(tmp_path: Path) -> Path:
    """A git repo whose crapkit project sits one directory down."""
    top = tmp_path / "repo"
    _project(top / "app")
    (top / "README.md").write_text("top-level file\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=top, check=True,
                   capture_output=True)
    _git(top, "add", "-A")
    _git(top, "commit", "-q", "-m", "init")
    return top


def _worklist(app: Path) -> dict:
    res = run_cli(app, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    res = run_cli(app, "worklist", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout)


def test_a_subdir_root_still_sees_its_files_churn(nested: Path):
    wl = _worklist(nested / "app")
    assert [e["path"] for e in wl["active"]] == ["core/hot.py"]
    assert wl["active"][0]["commits"] >= 1
    assert wl["dormant_count"] == 0, "fresh commits must never read as dormant"


def test_a_subdir_root_inside_a_linked_worktree_matches(nested: Path):
    """The reported setup exactly: the nested project checked out through
    `git worktree add`, where .git is a pointer file instead of a directory."""
    wt = nested.parent / "wt"
    _git(nested, "worktree", "add", "-b", "feature", str(wt))
    wl = _worklist(wt / "app")
    assert [e["path"] for e in wl["active"]] == ["core/hot.py"]
    assert wl["dormant_count"] == 0
