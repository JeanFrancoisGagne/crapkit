"""Reusable test inputs must preserve independent source and Git state."""
from pathlib import Path

import pytest

import test_release_guards as fixtures


def test_release_fixtures_prepare_once_and_keep_checkouts_and_remotes_private(tmp_path, monkeypatch):
    initializations = []
    original = fixtures.git

    def git(root, *arguments):
        if arguments[0] == "init":
            initializations.append(root)
        return original(root, *arguments)

    monkeypatch.setattr(fixtures, "git", git)
    first = fixtures.repo(tmp_path / "one", bumped=True)
    second = fixtures.repo(tmp_path / "two", bumped=True)
    head = original(second, "rev-parse", "HEAD")
    content = (second / "README.md").read_bytes()
    original(first, "switch", "-c", "changed")
    (first / "README.md").write_text("one private edit", encoding="utf-8")
    original(first, "add", "README.md")
    original(first, "commit", "-qm", "change only the first fixture")
    original(first, "push", "origin", "HEAD:main")
    assert original(second, "branch", "--show-current") == "main"
    assert original(second, "rev-parse", "HEAD") == head
    assert original(second, "status", "--porcelain") == ""
    assert (second / "README.md").read_bytes() == content
    assert original(second, "ls-remote", "origin", "refs/heads/main").split()[0] == head
    assert Path(original(second, "remote", "get-url", "origin")) == tmp_path / "two/remote.git"
    assert len(initializations) == 1, "prepare the shared pristine input only once"


def test_release_fixture_versions_keep_separate_pristine_inputs(tmp_path):
    old = fixtures.repo(tmp_path / "old")
    fresh = fixtures.repo(tmp_path / "fresh", bumped=True)
    assert '__version__ = "0.5.1"' in (old / "src/crapkit/__init__.py").read_text()
    assert '__version__ = "0.5.2"' in (fresh / "src/crapkit/__init__.py").read_text()
    assert not (fresh / ".crapkit/release-receipt.json").exists()


def test_release_fixture_retries_setup_after_an_interrupted_git_initialization(tmp_path, monkeypatch):
    initializations = []
    original = fixtures.git

    def interrupted(root, *arguments):
        result = original(root, *arguments)
        if arguments[0] == "init":
            initializations.append(root)
            if len(initializations) == 1:
                raise RuntimeError("interrupted fixture initialization")
        return result

    monkeypatch.setattr(fixtures, "git", interrupted)
    with pytest.raises(RuntimeError, match="interrupted fixture initialization"):
        fixtures.repo(tmp_path / "first", bumped=True)
    root = fixtures.repo(tmp_path / "retry", bumped=True)
    assert original(root, "rev-parse", "HEAD") == original(root, "ls-remote", "origin", "refs/heads/main").split()[0]
    assert original(root, "status", "--porcelain") == ""
    fixtures.repo(tmp_path / "warm", bumped=True)
    assert len(initializations) == 2
