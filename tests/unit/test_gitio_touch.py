"""A file whose bytes did not change is not a change, whatever the repo's diff config.

`git diff --name-only` answers from the index's stat cache. With
`diff.autoRefreshIndex` on, git's default, it checks the content of every
stat-dirty file through the repo's filters and drops the ones that still match.
With it off, it names every file whose mtime moved. crapkit reads the dirty set
that way for lane staleness, lane reuse and verify, so under that setting a
`touch` alone reported "1 file(s) in its scopes changed" and cost a rerun.

The checkout here holds CRLF under `core.autocrlf=true` while the blob holds LF:
a check that compared raw bytes instead of going through git would read every
such file as changed.

Real git processes, because the bug lives in the argv crapkit builds.
"""
import os
import subprocess
from pathlib import Path

import pytest

from crapkit.gitio import status_names, unstaged_paths
from crapkit.lane_changes import ChangeReads
from hang_guard import HANG_SECONDS


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=HANG_SECONDS, check=True).stdout


def _touch(path: Path) -> None:
    """A new mtime on the same bytes, two minutes past the file's own, so no
    stat cache matches it."""
    later = path.stat().st_mtime + 120
    os.utime(path, (later, later))


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """One CRLF source file committed under core.autocrlf=true, with the stat
    refresh off: the repo config a same-bytes touch used to read as an edit in."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "core.autocrlf", "true")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_bytes(b"export const a = 1;\r\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    _git(tmp_path, "config", "diff.autoRefreshIndex", "false")
    return tmp_path


def test_a_touched_file_is_not_dirty(repo):
    _touch(repo / "src" / "a.ts")

    assert status_names(repo) == []


def test_a_touched_file_is_not_unstaged(repo):
    _touch(repo / "src" / "a.ts")

    assert unstaged_paths(repo) == set()


def test_a_touched_file_is_not_a_change_under_a_lane_scope(repo):
    _touch(repo / "src" / "a.ts")

    with ChangeReads(repo, (), ("src",)) as reads:
        assert reads.status_names() == ()


def test_an_edit_is_still_a_change(repo):
    """The guard cuts both ways: new bytes stay dirty in every read."""
    (repo / "src" / "a.ts").write_bytes(b"export const a = 2;\r\n")

    with ChangeReads(repo, (), ("src",)) as reads:
        scoped = reads.status_names()
    assert (status_names(repo), unstaged_paths(repo), scoped) == (
        ["src/a.ts"], {"src/a.ts"}, ("src/a.ts",))


@pytest.fixture()
def default_repo(tmp_path: Path) -> Path:
    """One LF source file under git's default config, its index entry settled
    well before the index was written, so only a later touch makes it stat-dirty."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "a.ts"
    source.write_bytes(b"export const a = 1;\n")
    earlier = source.stat().st_mtime - 60
    os.utime(source, (earlier, earlier))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return tmp_path


def _reads_after_a_touch(repo: Path) -> None:
    from crapkit.gitio import worktree_changes

    _touch(repo / "src" / "a.ts")
    with ChangeReads(repo, (), ("src",)) as reads:
        reads.status_names()
    status_names(repo)
    worktree_changes(repo, ("src",))


def _environments(monkeypatch) -> list[dict]:
    """Every environment crapkit hands a git process from here on."""
    import subprocess as sp

    seen: list[dict] = []
    real_run, real_popen = sp.run, sp.Popen

    def run(argv, *args, **kwargs):
        seen.append(kwargs.get("env") or {})
        return real_run(argv, *args, **kwargs)

    class Popen(real_popen):
        def __init__(self, argv, *args, **kwargs):
            seen.append(kwargs.get("env") or {})
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(sp, "run", run)
    monkeypatch.setattr(sp, "Popen", Popen)
    return seen


def test_every_git_process_takes_no_optional_lock(default_repo, monkeypatch):
    """GIT_OPTIONAL_LOCKS=0 is git's own spelling of --no-optional-locks: a
    read never writes the index for its own convenience, and a write takes the
    lock it needs anyway."""
    from crapkit import gitio

    seen = _environments(monkeypatch)
    commit = gitio.head_commit(default_repo)
    gitio.is_ancestor(default_repo, commit)
    gitio.has_commit(default_repo, commit)
    gitio.is_shallow(default_repo)
    gitio.index_blobs(default_repo, ("src",))
    gitio.worktree_blobs(default_repo, ["src/a.ts"])
    gitio.staged_blobs(default_repo, ["src/a.ts"])
    list(gitio._git_lines(default_repo, "log", "--format=%H"))
    _reads_after_a_touch(default_repo)
    gitio.stage_path(default_repo, "src/a.ts")

    assert seen and all(env.get("GIT_OPTIONAL_LOCKS") == "0" for env in seen)
