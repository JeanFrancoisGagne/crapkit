"""A file whose bytes did not change is not a change, and asking leaves .git/index alone.

crapkit reads the uncommitted set for lane staleness, lane reuse, verify's dirty
tags and the mutation pool. It used to read the worktree half with `git diff
--name-only`, which answers from the index's stat cache. With
`diff.autoRefreshIndex` off, that names every file whose mtime moved, so a
`touch` or a copied checkout alone read as an edit: a reused lane printed "1
file(s) in its scopes changed" and next-item dropped every dark line. With it
on, git checks the content and then writes the refreshed index over .git/index,
whatever GIT_OPTIONAL_LOCKS says. crapkit starts its lane reads at once, and on
Windows a sibling read that opens the index during that rename fails with
"index file open failed: Permission denied": the lane then read stale or
stamped no proof on a tree nobody touched.

`git --no-optional-locks status` answers the same question, compares content
whatever diff.autoRefreshIndex says, and leaves .git/index as it found it.

The checkout holds CRLF under `core.autocrlf=true` while the blob holds LF: a
check that compared raw bytes instead of going through git would read every
such file as changed. Real git processes, because the bug lives in the argv
crapkit builds.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest

from crapkit.cli.claude_hook import _porcelain
from crapkit.gitio import status_names, unstaged_paths
from crapkit.lane_changes import ChangeReads
from hang_guard import HANG_SECONDS


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=HANG_SECONDS, check=True).stdout


def _touch(path: Path) -> None:
    """A new mtime on the same bytes, far enough ahead that no stat cache matches it."""
    later = time.time() + 120
    os.utime(path, (later, later))


def _scoped(root: Path) -> tuple:
    with ChangeReads(root, (), ("src",)) as reads:
        return reads.status_names()


READERS = {"status_names": lambda root: tuple(status_names(root)),
           "unstaged_paths": lambda root: tuple(sorted(unstaged_paths(root))),
           "ChangeReads": _scoped}


def _commit_one_crlf_file(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "core.autocrlf", "true")
    (root / "src").mkdir()
    (root / "src" / "a.ts").write_bytes(b"export const a = 1;\r\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """One CRLF source file committed under core.autocrlf=true, with the stat
    refresh off: the repo config a same-bytes touch used to read as an edit in."""
    _commit_one_crlf_file(tmp_path)
    _git(tmp_path, "config", "diff.autoRefreshIndex", "false")
    return tmp_path


@pytest.mark.parametrize("reader", sorted(READERS))
def test_a_touched_file_is_no_change(repo, reader):
    _touch(repo / "src" / "a.ts")

    assert READERS[reader](repo) == ()


@pytest.mark.parametrize("reader", sorted(READERS))
def test_an_edit_is_still_a_change(repo, reader):
    """The guard cuts both ways: new bytes stay dirty in every read."""
    (repo / "src" / "a.ts").write_bytes(b"export const a = 2;\r\n")

    assert READERS[reader](repo) == ("src/a.ts",)


def _index_state(root: Path) -> tuple:
    """git writes a refreshed index to index.lock and renames it over .git/index,
    which gives the file a new id and a new mtime."""
    stat = os.stat(root / ".git" / "index")
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


@pytest.mark.parametrize("reader", sorted(READERS))
def test_a_read_leaves_the_index_as_it_found_it(tmp_path, reader):
    """git's default config, where a worktree `git diff` refreshes the index of
    a touched file and writes it back: the write that raced crapkit's own
    concurrent reads on Windows."""
    _commit_one_crlf_file(tmp_path)
    _touch(tmp_path / "src" / "a.ts")
    before = _index_state(tmp_path)

    assert READERS[reader](tmp_path) == ()
    assert _index_state(tmp_path) == before


def test_the_advisory_hooks_status_read_leaves_the_index_alone(tmp_path):
    """The Claude Code hook asks git status after every shell command, beside
    whatever git commands the agent runs next: the read git-status(1) tells to
    take --no-optional-locks."""
    _commit_one_crlf_file(tmp_path)
    _touch(tmp_path / "src" / "a.ts")
    before = _index_state(tmp_path)

    assert _porcelain(tmp_path) == ""
    assert _index_state(tmp_path) == before


def test_status_names_each_kind_of_change_from_a_root_below_the_top(tmp_path):
    """git status names every path from the repo top; the readers hand back
    paths relative to crapkit's root, the way ls-files spells them there."""
    _commit_one_crlf_file(tmp_path)
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    for name in ("staged.py", "edited.py", "gone.py"):
        (root / "src" / name).write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "app")
    (root / "src" / "staged.py").write_text("x = 2\n", encoding="utf-8")
    _git(root, "add", "src/staged.py")
    (root / "src" / "edited.py").write_text("x = 3\n", encoding="utf-8")
    (root / "src" / "gone.py").unlink()
    (root / "src" / "new.py").write_text("x = 4\n", encoding="utf-8")
    (tmp_path / "src" / "a.ts").write_bytes(b"outside the root\r\n")

    assert _scoped(root) == ("src/edited.py", "src/gone.py", "src/new.py", "src/staged.py")
    assert status_names(root) == ["src/edited.py", "src/gone.py", "src/new.py", "src/staged.py"]
    assert unstaged_paths(root) == {"src/edited.py", "src/gone.py"}


def test_a_path_both_deleted_from_the_index_and_left_untracked_is_named_once(tmp_path):
    """`git rm --cached` makes git status print the file twice, once staged as
    deleted and once untracked."""
    _commit_one_crlf_file(tmp_path)
    _git(tmp_path, "rm", "-q", "--cached", "src/a.ts")

    assert status_names(tmp_path) == ["src/a.ts"]
    assert _scoped(tmp_path) == ("src/a.ts",)
    assert unstaged_paths(tmp_path) == set()
