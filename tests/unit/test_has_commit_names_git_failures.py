"""gitio.has_commit answers whether this clone holds a commit, and raises
GitError when git cannot answer.

It used to read any nonzero exit as "not held": outside a repository, or on a
corrupt object, a reader then sent the operator after a fetch or an
unshallow that could not help. Exit 1 from `rev-parse --verify --quiet` is
git's own "no such commit"; every other exit is git failing to answer.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.errors import GitError
from crapkit.lane_freshness import _not_behind

MISSING = "deadbeef" * 5


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    return root


def commit(root: Path, message: str) -> str:
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def corrupt(root: Path, sha: str) -> None:
    """Overwrite the commit's loose object with bytes zlib refuses."""
    loose = root / ".git" / "objects" / sha[:2] / sha[2:]
    loose.chmod(0o644)
    loose.write_bytes(b"garbage")


def test_a_held_commit_is_held(repo):
    assert gitio.has_commit(repo, commit(repo, "one")) is True


@pytest.mark.parametrize("asked", [MISSING, "HEAD"])
def test_a_commit_this_clone_lacks_is_not_held(repo, asked):
    """A collected commit and HEAD before the first commit are both git's exit 1."""
    assert gitio.has_commit(repo, asked) is False


def test_outside_a_repository_git_failing_is_raised_not_read_as_missing(tmp_path):
    with pytest.raises(GitError, match="not a git repository"):
        gitio.has_commit(tmp_path, MISSING)


def test_a_corrupt_commit_object_is_raised_not_read_as_missing(repo):
    old = commit(repo, "one")
    commit(repo, "two")
    corrupt(repo, old)

    with pytest.raises(GitError, match=f"{old}.*corrupt"):
        gitio.has_commit(repo, old)


def test_lane_freshness_names_gits_error_where_it_said_the_clone_lacks_the_commit(repo):
    """A lane stamp's commit that is not behind HEAD: when git cannot answer,
    the reason quotes git, and sends no one after a fetch."""
    old = commit(repo, "one")
    commit(repo, "two")
    corrupt(repo, old)

    why = _not_behind(repo, old)

    assert why.startswith(f"git cannot say which files in its scopes changed since {old[:11]} ("), why
    assert "corrupt" in why and "does not hold" not in why, why
