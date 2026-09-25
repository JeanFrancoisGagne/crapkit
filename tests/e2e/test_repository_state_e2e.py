"""A command run where git can answer nothing says what the repository lacks.

Three field reports, one per state. The 60-second start in a fresh `git init`
repo passed `init` and `doctor` and then `coverage` exited 4 on git's own
"ambiguous argument 'HEAD': unknown revision". `verify` in a copied tree with no
`.git` printed 129 lines of `git diff --no-index` usage and never said the
directory was not a repository. The same tree copied into a fresh `git init`
was told a rebase had rewritten its baseline. Each now exits 4 with one line
that names the directory and the next step.
"""
import shutil
from pathlib import Path

import pytest

from conftest import git, git_commit_all, git_init_repo, run_cli

CONFIG = ('[crapkit]\n[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n'
          'coverage_optional = true\n')
SOURCE = "def grade(score, late):\n    if score > 90 and not late:\n        return 'A'\n    return 'B'\n"


def _tree(root: Path) -> Path:
    (root / "calc").mkdir(parents=True)
    (root / "calc" / "grade.py").write_text(SOURCE, encoding="utf-8")
    (root / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    return root


@pytest.fixture()
def measured(tmp_path: Path) -> Path:
    """A committed repo with a passing verify behind it, the tree a copy starts from."""
    repo = git_init_repo(_tree(tmp_path / "repo"))
    git_commit_all(repo, "calc")
    assert run_cli(repo, "coverage").returncode == 0
    assert run_cli(repo, "verify").returncode == 0
    return repo


def _copy_without_git(repo: Path, dest: Path) -> Path:
    """What a tarball or a copied tree holds: the files and the store, no `.git`."""
    shutil.copytree(repo, dest, ignore=shutil.ignore_patterns(".git"))
    return dest


@pytest.mark.parametrize("command", ["coverage", "inventory"])
def test_before_the_first_commit_the_start_says_to_make_it(tmp_path, command):
    repo = git_init_repo(_tree(tmp_path / "repo"))
    git(repo, "add", "-A")

    res = run_cli(repo, command)

    assert res.returncode == 4, res.stdout + res.stderr
    assert res.stderr == (f"crapkit: the git repository at {repo} has no commit yet: crapkit "
                          "measures a commit, so make the first one (git add, then git commit) "
                          "and run it again\n")


@pytest.mark.parametrize("command", ["verify", "hook-precommit"])
def test_a_copied_tree_with_no_repository_says_so_in_one_line(measured, tmp_path, monkeypatch, command):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    copy = _copy_without_git(measured, tmp_path / "copy")

    res = run_cli(copy, command)

    assert res.returncode == 4, res.stdout + res.stderr
    assert res.stderr == (f"crapkit: {copy} is not a git repository, and no directory above it "
                          "is one: crapkit reads the files it scores and the commit it measures "
                          "from git, so run it inside a checkout, or run git init, git add and "
                          "git commit here first\n")


def test_a_store_copied_into_a_fresh_repository_blames_the_missing_commit(measured, tmp_path):
    """verify asked whether the baseline commit sits behind HEAD, read git's
    failure as no, and said a rebase or amend had rewritten history."""
    copy = git_init_repo(_copy_without_git(measured, tmp_path / "copy"))
    git(copy, "add", "-A")

    res = run_cli(copy, "verify")

    assert res.returncode == 4, res.stdout + res.stderr
    assert "rewrote history" not in res.stderr
    assert f"the git repository at {copy} has no commit yet" in res.stderr
