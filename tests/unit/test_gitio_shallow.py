"""`is_shallow`: whether this checkout is a depth-limited clone.

verify's ancestor check reads it when a baseline commit is not behind HEAD: in a
shallow clone the commit is simply not there, and the fix is a deeper fetch, not
the fresh baseline the rewrite message asks for. Real git in tmp_path, the way
the merge-base tests do it: the answer decides which hint a CI log prints.
"""
import subprocess
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.gitio import GitFacts, is_shallow


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", name)


@pytest.fixture()
def full(tmp_path: Path) -> Path:
    """Two commits, so a depth-1 clone of it leaves one behind."""
    repo = tmp_path / "full"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit(repo, "one")
    commit(repo, "two")
    return repo


@pytest.fixture()
def shallow(full: Path, tmp_path: Path) -> Path:
    """`--no-local` on purpose: git ignores --depth on a plain local clone, and
    the `file://` spelling that also forces the transport reads a Windows drive
    letter as a path component."""
    clone = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", "--no-local", str(full), str(clone))
    return clone


def test_a_full_checkout_is_not_shallow(full: Path):
    assert is_shallow(full) is False


def test_a_depth_one_clone_is_shallow(shallow: Path):
    assert is_shallow(shallow) is True


def test_git_facts_asks_once_per_command(shallow: Path, monkeypatch):
    """Like the ancestry answers beside it: a clone does not deepen under a
    running verify, so the second read costs no spawn."""
    calls = []
    monkeypatch.setattr(gitio, "is_shallow", lambda root: calls.append(root) or True)
    facts = GitFacts(shallow)

    assert facts.is_shallow() is True
    assert facts.is_shallow() is True
    assert len(calls) == 1
