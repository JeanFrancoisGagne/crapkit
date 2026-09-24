"""An edit git's diff does not compare is still a change in crapkit's dirty set.

`git diff` and `git status` skip three kinds of edit by design: a file flagged
`--skip-worktree`, a file flagged `--assume-unchanged`, and a file inside a
submodule whose `.gitmodules` entry says `ignore = dirty`. crapkit read its
dirty set from those commands, so lane reuse republished coverage the edited
tests no longer earn, verify called an edited file's finding committed, and
`mutate` copied the index's version of the test into every worker.

Real git processes, because the bug lives in what git leaves out.
"""
import os
import subprocess
from pathlib import Path

import pytest

from crapkit import gitio, lane_changes
from crapkit.gitio import status_names
from crapkit.lane_changes import ChangeReads
from hang_guard import HANG_SECONDS


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                           "-c", "protocol.file.allow=always", *args], cwd=repo,
                          capture_output=True, text=True, timeout=HANG_SECONDS,
                          check=True).stdout


def _repo(root: Path, files: dict[str, bytes]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _scoped(root: Path, *paths: str) -> tuple[str, ...]:
    with ChangeReads(root, (), paths) as reads:
        return reads.status_names()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "repo", {"src/a.py": b"a = 1\n", "tests/test_a.py": b"assert 1\n"})


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_an_edit_to_a_flagged_file_is_a_change_in_every_dirty_read(repo, flag):
    _git(repo, "update-index", flag, "tests/test_a.py")
    (repo / "tests/test_a.py").write_bytes(b"assert 2\n")

    assert _git(repo, "diff", "--name-only") == ""
    assert (status_names(repo), _scoped(repo, "tests")) == (["tests/test_a.py"], ("tests/test_a.py",))


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_a_flagged_file_holding_its_index_bytes_is_not_a_change(repo, flag):
    """A touch moves no byte, and a CRLF checkout under core.autocrlf=true
    hashes to its LF blob through git's filters."""
    _git(repo, "config", "core.autocrlf", "true")
    _git(repo, "update-index", flag, "tests/test_a.py")
    (repo / "tests/test_a.py").write_bytes(b"assert 1\r\n")
    later = (repo / "tests/test_a.py").stat().st_mtime + 120
    os.utime(repo / "tests/test_a.py", (later, later))

    assert (status_names(repo), _scoped(repo, "tests")) == ([], ())


def test_a_skip_worktree_file_outside_a_sparse_cone_is_not_a_change(repo):
    """Sparse checkout flags the files outside its cone and leaves them off
    the disk; a missing flagged file is not an edit."""
    _git(repo, "update-index", "--skip-worktree", "tests/test_a.py")
    (repo / "tests/test_a.py").unlink()

    assert gitio.hidden_edits(repo) == [] and status_names(repo) == []


def test_only_flagged_files_under_the_paths_are_read(repo):
    for rel in ("src/a.py", "tests/test_a.py"):
        _git(repo, "update-index", "--skip-worktree", rel)
        (repo / rel).write_bytes(b"edited = True\n")

    assert (gitio.hidden_edits(repo, "src"), _scoped(repo, "src")) == (["src/a.py"], ("src/a.py",))


def test_an_edit_inside_a_submodule_set_to_ignore_dirty_is_a_change(tmp_path):
    """The submodule sits under the lane's inputs; `ignore = dirty` in
    .gitmodules hid the edit from `git diff` and reuse kept the old coverage."""
    library = _repo(tmp_path / "library", {"lib.py": b"def f():\n    return 1\n"})
    root = _repo(tmp_path / "repo", {"src/a.py": b"a = 1\n"})
    _git(root, "submodule", "--quiet", "add", library.as_uri(), "sub")
    _git(root, "config", "-f", ".gitmodules", "submodule.sub.ignore", "dirty")
    _git(root, "commit", "-qam", "sub")
    (root / "sub/lib.py").write_bytes(b"def f():\n    return 2\n")

    assert _git(root, "diff", "--name-only") == ""
    assert (status_names(root), _scoped(root, "src", "sub")) == (["sub"], ("sub",))


def test_the_index_reads_start_once_the_worktree_diff_is_collected(repo, monkeypatch):
    """The worktree diff rewrites .git/index when it refreshes a stat-dirty
    entry. On Windows a read that opened the index during that swap failed
    with `index file open failed: Permission denied`, and the failure read as
    a changed file: 7 to 12 of 300 same-bytes touches on a default config."""
    events = []
    real = lane_changes._start

    class _Logged:
        def __init__(self, read, name):
            self._read, self._name = read, name

        def result(self, *payload):
            events.append(("collect", self._name))
            return self._read.result(*payload)

    def start(root, *args):
        name = " ".join(arg for arg in args if arg in ("diff", "--cached", "ls-files"))
        events.append(("start", name))
        return _Logged(real(root, *args), name)

    monkeypatch.setattr(lane_changes, "_start", start)

    assert _scoped(repo, "src") == ()
    assert events[:2] == [("start", "diff"), ("collect", "diff")]
    assert {name for kind, name in events[2:] if kind == "start"} == {"diff --cached", "ls-files"}
