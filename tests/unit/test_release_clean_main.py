"""The release reads branch, HEAD and dirty state from one `git status`.

`_clean_main` guards every stage and every publish action, and it spawned three
git processes each time: `branch --show-current`, `status --porcelain` and
`rev-parse HEAD`. Porcelain v2 with `--branch` carries all three answers.
"""
import pytest

from test_release_guards import git, repo
from test_release_tool import release

STATUS = ("status", "--porcelain=v2", "--branch", "--untracked-files=all", "-z")


def _counted(monkeypatch):
    calls = []
    real = release._git

    def git_call(root, *arguments):
        calls.append(arguments)
        return real(root, *arguments)

    monkeypatch.setattr(release, "_git", git_call)
    return calls


def test_clean_main_answers_from_one_git_status(tmp_path, monkeypatch):
    root = repo(tmp_path)
    calls = _counted(monkeypatch)

    head = release._clean_main(root)

    assert calls == [STATUS]
    assert head == git(root, "rev-parse", "HEAD")


def _dirty(root, state):
    if state == "untracked":
        (root / "new file.txt").write_text("new", encoding="utf-8")
    elif state == "modified":
        (root / "README.md").write_text("changed", encoding="utf-8")
    elif state == "renamed":
        # A rename record carries its original path as its own NUL field, and
        # this one starts with "# ", the way porcelain v2 headers do.
        (root / "# notes").write_text("notes", encoding="utf-8")
        git(root, "add", "# notes")
        git(root, "commit", "-qm", "notes")
        git(root, "mv", "# notes", "notes")
    else:
        (root / "README.md").write_text("staged", encoding="utf-8")
        git(root, "add", "README.md")


@pytest.mark.parametrize("state", ["untracked", "modified", "staged", "renamed"])
def test_a_dirty_tree_is_refused(tmp_path, state):
    root = repo(tmp_path)
    _dirty(root, state)

    with pytest.raises(release.ReleaseError, match="clean tree"):
        release._clean_main(root)


@pytest.mark.parametrize("move", [("switch", "-c", "feature"), ("switch", "--detach")])
def test_anything_but_the_main_branch_is_refused(tmp_path, move):
    root = repo(tmp_path)
    git(root, *move)

    with pytest.raises(release.ReleaseError, match="main branch"):
        release._clean_main(root)


def test_an_unborn_main_is_refused(tmp_path):
    root = tmp_path / "unborn"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")

    with pytest.raises(release.ReleaseError, match="requires a commit on main"):
        release._clean_main(root)
