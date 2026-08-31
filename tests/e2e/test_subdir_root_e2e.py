"""Churn when the crapkit root is a subdirectory of the git working tree.

The field shape: a project living at `<repo>/app` (monorepo member, or an
agent-managed linked worktree whose checkout nests the project one level down).
Scored rows are root-relative because `git ls-files` answers relative to its
cwd, but `git log --name-only` answers relative to the repo top — `app/src/x.py`
against rows saying `src/x.py`. Every lookup missed, every file read as
zero-churn, and worklist filed the whole corpus under dormant.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

MAKE_COV = ('import json\n'
            'json.dump({"meta": {"branch_coverage": True}, "files": {}},'
            ' open("cov.json", "w"))\n')

CONFIG = """[crapkit]
target = 6
worklist_floor = 5
mutation_command = "python -c pass"

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


run_cli = cli_runner(timeout=180)


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


# --- the staged diff, which reads the same way ------------------------------
#
# `git diff` prints paths relative to the repo TOP too, so every reader that
# joins a diff path against a scored row missed under a nested root: the commit
# gate passed a ccn 12 function, `rescore --gate` and `verify` scored it and
# gated nothing, lane reuse read a changed scope as unchanged and published
# coverage from a stale artifact, and mutate resolved none of its targets on
# disk. All of them are the one shape below.


def _run_hook(app: Path, payload: dict) -> subprocess.CompletedProcess:
    return run_cli(app, "claude-hook", "--protocol", "1", stdin=json.dumps(payload))


def _stage_breach(nested: Path) -> Path:
    """core/hot.py rewritten to ccn 12 and staged. Returns the crapkit root."""
    app = nested / "app"
    (app / "core" / "hot.py").write_text(_source(11), encoding="utf-8")
    _git(nested, "add", "app/core/hot.py")
    return app


def test_a_subdir_root_gates_a_staged_function_over_the_ceiling(nested: Path):
    """The issue: the pre-commit gate let a ccn 12 function through and said the
    file belonged to no scope, because the diff named it `app/core/hot.py` while
    the scope claims `core/`."""
    app = _stage_breach(nested)

    res = run_cli(app, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert "core/hot.py:1" in res.stdout
    assert "app/core/hot.py" not in res.stdout
    assert "belong to no scope" not in res.stderr


def test_a_subdir_root_reads_the_staged_blob_it_gates(nested: Path):
    """`git cat-file --batch` reads `:<path>` from the repo top whatever the cwd,
    so a root-relative request needs the `./` gitrevisions spells out. Both
    readers build it: the prefetched pair the hook uses and the fallback."""
    from crapkit import gitio

    app = _stage_breach(nested)

    with gitio.staged_reads(app) as reads:
        prefetched = reads.staged_blobs(["core/hot.py"])
    fallback = gitio.staged_blobs(app, ["core/hot.py"])

    assert b"def hot(a, b):" in prefetched["core/hot.py"]
    assert fallback == prefetched


def test_a_subdir_root_advises_on_an_edited_file(nested: Path):
    """The claude-hook sibling: it spawns its own diff, so gitio's flag does not
    reach it. stdout stays empty either way — protocol 1 reserves it."""
    app = _stage_breach(nested)

    res = _run_hook(app, {"hook_event_name": "PostToolUse", "tool_name": "Edit",
                          "cwd": str(app),
                          "tool_input": {"file_path": str(app / "core" / "hot.py")}})

    assert res.returncode == 2, res.stderr
    assert "core/hot.py:1" in res.stderr
    assert res.stdout == ""


@pytest.mark.parametrize("where", [".", "app"])
def test_a_subdir_root_advises_on_a_bash_written_file(nested: Path, where: str):
    """The Bash fallback under the nested layout: a heredoc rewrote the file, so
    the event names no file_path. The fallback walks the dirty tree from the git
    top, whatever directory the command ran from, and still judges each file by
    the crapkit root it sits under."""
    app = nested / "app"
    (app / "core" / "hot.py").write_text(_source(11), encoding="utf-8")

    res = _run_hook(app, {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                          "cwd": str(nested / where),
                          "tool_input": {"command": "python - <<'PY'\nPY"}})

    assert res.returncode == 2, res.stderr
    assert "core/hot.py:1" in res.stderr
    assert "app/core/hot.py" not in res.stderr
    assert res.stdout == ""


def test_a_subdir_root_gates_on_rescore_and_verify(nested: Path):
    """Both scoring gates filter on `diff_since(root, ...)`, so under a nested
    root no scored row was ever a candidate and both exited 0 on a crap 156 row."""
    app = _stage_breach(nested)
    assert run_cli(app, "coverage").returncode == 0

    gated = run_cli(app, "rescore", "--gate", "core/hot.py")
    verified = run_cli(app, "verify")

    assert gated.returncode == 6, gated.stdout + gated.stderr
    assert verified.returncode == 6, verified.stdout + verified.stderr


def test_a_subdir_root_reruns_a_lane_whose_scope_changed(nested: Path):
    """The one sibling that publishes a wrong number rather than skipping a
    check: reuse read every scope as unchanged and scored against stale
    coverage."""
    app = nested / "app"
    assert run_cli(app, "coverage").returncode == 0
    _git(nested, "add", "-A")
    _git(nested, "commit", "-q", "-m", "scored")
    (app / "core" / "hot.py").write_text(_source(7), encoding="utf-8")

    res = run_cli(app, "coverage", "--reuse-unchanged")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "still matches its scopes" not in res.stderr


def test_a_subdir_root_reruns_a_lane_for_a_new_untracked_file(nested: Path):
    """A file git has never seen is the most common way a lane goes stale, and
    `git status --untracked-files=no` could not see it. The union that replaced
    it asks `ls-files --others` too."""
    app = nested / "app"
    assert run_cli(app, "coverage").returncode == 0
    _git(nested, "add", "-A")
    _git(nested, "commit", "-q", "-m", "scored")
    (app / "core" / "fresh.py").write_text(_source(1), encoding="utf-8")

    res = run_cli(app, "coverage", "--reuse-unchanged")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "still matches its scopes" not in res.stderr


def test_a_subdir_root_mutates_the_lines_it_changed(nested: Path):
    """mutate built targets from top-relative diff keys, then read `<root>/app/
    core/hot.py`, missed every one and reported a mutation score over zero
    mutants."""
    app = nested / "app"
    (app / "core" / "hot.py").write_text(_source(7), encoding="utf-8")

    res = run_cli(app, "mutate", "--json", "--max-mutants", "2")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["mutants"] > 0
