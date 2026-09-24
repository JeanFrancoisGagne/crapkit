"""The watch loop driven to a known end.

`--cycles N` is the seam that makes the loop testable at all. The unbounded
default only ever ends in a signal, and a watcher killed with SIGTERM proves
nothing about its exit code and writes no coverage on the way out.

No wall-clock waits anywhere: a real subprocess that stops on its own,
in-process runs whose only stub is the clock (each change lands during the
poll's own sleep, and the rescore that fires is a real child process), and an
interrupt during that same sleep.

A poll reads an mtime as the fast path and content as the verdict: a touch or a
save of the same bytes is no change, an edit and a new file in a scope are. A
same-size edit put back under its old mtime is the one change a poll cannot
see, the named limit it shares with the analysis stat index.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest

from crapkit.cli import admin
from crapkit.cli.parser import build_parser
from crapkit.cli.admin import cmd_watch
from crapkit.errors import GitError

from conftest import cli_runner

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


run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


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


def _later(path: Path) -> None:
    """A fixed offset off the file's own mtime: a poll only asks whether its
    two snapshots differ, so nothing here depends on the wall clock."""
    later = path.stat().st_mtime + 60
    os.utime(path, (later, later))


def _edit(repo: Path) -> None:
    app = repo / "src" / "app.py"
    write(app, APP.replace("b += 1", "b += 2") + "\n\ndef extra(n):\n    return n\n")
    _later(app)


def _touch(repo: Path) -> None:
    _later(repo / "src" / "app.py")


def _same_bytes(repo: Path) -> None:
    app = repo / "src" / "app.py"
    app.write_bytes(app.read_bytes())
    _later(app)


def _restored_mtime(repo: Path) -> None:
    """cp -p, touch -r, robocopy: new bytes of the same length under the old mtime."""
    app = repo / "src" / "app.py"
    kept = app.stat()
    write(app, APP.replace("b += 1", "b -= 1"))
    os.utime(app, ns=(kept.st_atime_ns, kept.st_mtime_ns))


def _add_in_scope(repo: Path) -> None:
    write(repo / "src" / "fresh.py", "def fresh(n):\n    if n:\n        return 1\n    return 0\n")


def _add_ignored(repo: Path) -> None:
    write(repo / ".gitignore", ".crapkit/\ncov.json\n__pycache__/\nsrc/scratch.py\n")
    write(repo / "src" / "scratch.py", "def scratch(n):\n    return n\n")


def _add_outside_scopes(repo: Path) -> None:
    write(repo / "tools" / "helper.py", "def helper(n):\n    return n\n")


def _delete(repo: Path) -> None:
    (repo / "src" / "app.py").unlink()


@pytest.mark.parametrize("change, announced, rescored", [
    (_edit, "--- changed: src/app.py", "extra"),
    (_add_in_scope, "--- changed: src/fresh.py", "fresh"),
    (_touch, None, None),
    (_same_bytes, None, None),
    (_add_ignored, None, None),
    (_add_outside_scopes, None, None),
    (_delete, None, None),
    # The Q38 limit: a poll takes an unmoved mtime as unchanged content.
    (_restored_mtime, None, None),
], ids=["edit", "add-in-scope", "touch", "same-bytes-rewrite", "add-ignored",
        "add-outside-scopes", "delete", "same-size-restored-mtime-limit"])
def test_a_poll_rescores_what_changed_content_during_it(
        scored_repo: Path, monkeypatch, capfd, change, announced, rescored):
    monkeypatch.setattr(time, "sleep", lambda seconds: change(scored_repo))

    assert cmd_watch(watch_args(scored_repo, "--cycles", "1")) == 0

    out = capfd.readouterr().out
    changed = [line for line in out.splitlines() if line.startswith("--- changed")]
    assert changed == ([announced] if announced else []), out
    if rescored:
        assert rescored in out, "the child rescore ran and its table reached the watcher's output"
    else:
        assert "rescore vs run" not in out, "nothing changed, so nothing was rescored"


def test_a_touch_then_an_edit_rescores_once_on_the_edit(scored_repo: Path, monkeypatch, capfd):
    """The touch leaves the recorded content alone, so the edit a poll later is
    still judged against the bytes the watch started from."""
    changes = iter([_touch, _edit])
    monkeypatch.setattr(time, "sleep", lambda seconds: next(changes)(scored_repo))

    assert cmd_watch(watch_args(scored_repo, "--cycles", "2")) == 0

    out = capfd.readouterr().out
    assert out.count("--- changed") == 1 and "--- changed: src/app.py" in out, out


def test_a_relisting_git_refuses_keeps_the_last_list_and_names_the_error_once(
        scored_repo: Path, monkeypatch, capfd):
    """git failing between two polls (a lock, a moved checkout) is not "no
    files": the watcher keeps what it had, says why once, and still sees an edit."""
    listed = admin._watched_files(scored_repo, admin._load_repo_config(scored_repo))
    answers = iter([listed])

    def relist(root, cfg):
        for files in answers:
            return files
        raise GitError("git ls-files failed in repo: fatal: index file corrupt")

    monkeypatch.setattr(admin, "_watched_files", relist)
    changes = iter([_touch, lambda repo: None, _edit])
    monkeypatch.setattr(time, "sleep", lambda seconds: next(changes)(scored_repo))

    assert cmd_watch(watch_args(scored_repo, "--cycles", "3")) == 0

    out = capfd.readouterr().out
    assert out.count("index file corrupt") == 1, out
    assert "--- changed: src/app.py" in out, "the edit on the kept list is still rescored"


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


def test_a_repo_wide_scope_lists_new_files_anywhere_and_never_the_ignored_ones(repo: Path):
    """A poll lists under the declared scope paths, and `.` declares the root."""
    write(repo / "crapkit.toml", TOML.replace('paths = ["src"]', 'paths = ["."]'))
    write(repo / "top.py", "def top(n):\n    return n\n")
    write(repo / "__pycache__" / "cached.py", "def cached(n):\n    return n\n")

    files = admin._watched_files(repo, admin._load_repo_config(repo))

    assert {"top.py", "src/app.py", "make_cov.py"} <= set(files)
    assert not [f for f in files if "__pycache__" in f], "git's ignore rules apply"
