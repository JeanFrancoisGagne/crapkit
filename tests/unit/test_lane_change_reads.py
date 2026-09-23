"""ChangeReads: git's answers about paths under a stamp, started together."""
import subprocess
from pathlib import Path

import pytest

from crapkit import lane_changes
from crapkit.errors import GitError
from crapkit.lane_changes import ChangeReads


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=60, check=True).stdout


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path: Path):
    _write(tmp_path, "src/a.py")
    _write(tmp_path, "docs/n.md")
    _git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path, _commit(tmp_path, "init")


def test_every_kind_of_change_under_the_paths_is_named_and_nothing_outside(repo):
    root, first = repo
    _write(root, "src/a.py", "committed\n")
    _write(root, "docs/n.md", "committed outside\n")
    _commit(root, "second")
    _write(root, "src/staged.py")
    _git(root, "add", "src/staged.py")
    _write(root, "src/untracked.py")
    _write(root, "docs/untracked.md")

    with ChangeReads(root, (first,), ("src",)) as reads:
        assert reads.is_ancestor(first) is True
        assert reads.changed_since(first) == ("src/a.py", "src/staged.py", "src/untracked.py")


def test_no_paths_means_nothing_can_change_and_no_diff_starts(repo, monkeypatch):
    root, first = repo
    _write(root, "src/untracked.py")
    started = []
    real = lane_changes._start
    monkeypatch.setattr(lane_changes, "_start",
                        lambda r, *args: (started.append(args[0]), real(r, *args))[1])

    with ChangeReads(root, (first,), ()) as reads:
        assert reads.changed_since(first) == ()
        assert reads.is_ancestor(first) is True

    assert started == ["merge-base"]


def test_a_commit_it_was_not_built_for_is_read_when_asked(repo):
    root, first = repo
    _write(root, "src/a.py", "moved\n")
    second = _commit(root, "second")

    with ChangeReads(root, (second,), ("src",)) as reads:
        assert reads.is_ancestor(first) is True
        assert reads.diff_names_since(first) == ("src/a.py",)
        assert reads.diff_names_since(second) == ()


def test_a_commit_this_clone_does_not_hold_is_not_behind_head(repo):
    root, _ = repo
    with ChangeReads(root, ("0" * 40,), ("src",)) as reads:
        assert reads.is_ancestor("0" * 40) is False


def test_a_failed_spawn_waits_for_the_reads_already_started(repo, monkeypatch):
    root, first = repo
    started = []
    real = lane_changes._start

    def flaky(r, *args):
        if len(started) == 2:
            raise GitError("git executable not found")
        read = real(r, *args)
        started.append(read)
        return read

    monkeypatch.setattr(lane_changes, "_start", flaky)

    with pytest.raises(GitError):
        ChangeReads(root, (first,), ("src",))

    assert len(started) == 2
    assert all(read._proc.returncode is not None for read in started), "each one was reaped"
