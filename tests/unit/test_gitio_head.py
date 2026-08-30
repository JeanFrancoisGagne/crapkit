"""HEAD is read out of .git, not asked of a git process.

Every command starts by asking where HEAD is, and on Windows that spawn costs
~20ms — for a 40-character string sitting in a file. The reader below answers
from the ref files and falls back to the spawn on anything it does not fully
understand, so a shape it has never seen degrades to being slow, never wrong.

The fallback is counted, not assumed: a test that only checked the answer would
pass just as happily if the fast path never fired at all.
"""
import subprocess
from pathlib import Path

import pytest

from crapkit import gitio
from crapkit.errors import GitError
from crapkit.gitio import head_commit


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


@pytest.fixture()
def spawns(monkeypatch) -> list:
    """Every git process gitio starts, recorded and still run."""
    started: list = []
    real = gitio._git

    def counted(root, *args):
        started.append(args)
        return real(root, *args)

    monkeypatch.setattr(gitio, "_git", counted)
    return started


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "one.txt").write_text("one", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "one")
    return tmp_path


def test_a_branch_head_is_read_from_the_ref_file(repo, spawns):
    assert head_commit(repo) == git(repo, "rev-parse", "HEAD")
    assert spawns == [], "the answer was in .git/refs/heads/main"


def test_a_detached_head_is_the_sha_in_the_head_file(repo, spawns):
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "--detach")

    assert head_commit(repo) == sha
    assert spawns == []


def test_a_packed_ref_is_found_without_a_spawn(repo, spawns):
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "pack-refs", "--all")
    assert not (repo / ".git" / "refs" / "heads" / "main").exists(), "git kept the loose ref"

    assert head_commit(repo) == sha
    assert spawns == []


def test_a_worktrees_git_file_points_at_its_own_head(repo, tmp_path, spawns):
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "--detach", str(linked))
    assert (linked / ".git").is_file(), "a linked worktree carries a gitdir pointer, not a directory"

    assert head_commit(linked) == git(linked, "rev-parse", "HEAD")
    assert spawns == []


def test_a_worktree_on_a_branch_reads_the_shared_ref(repo, tmp_path, spawns):
    """The worktree's admin directory holds HEAD; refs/heads lives in the common dir."""
    linked = tmp_path / "branched"
    git(repo, "worktree", "add", "-q", "-b", "side", str(linked))

    assert head_commit(linked) == git(linked, "rev-parse", "HEAD")
    assert spawns == []


def test_a_root_below_the_repo_top_reads_head_without_a_spawn(repo, spawns):
    """The layout PR #23 shipped support for: the crapkit root one directory
    under the git top. .git sits above it, so only a walk up finds it, and every
    command that asks for HEAD free-standing paid a process here."""
    below = repo / "pkg" / "nested"
    below.mkdir(parents=True)

    assert head_commit(below) == git(repo, "rev-parse", "HEAD")
    assert spawns == [], "the answer was in the top's .git, two directories up"


def test_a_root_inside_a_submodule_stops_at_the_submodules_own_git(repo, tmp_path, spawns):
    """The walk may not cross a repo boundary, and a `gitdir:` pointer is
    relative to the directory holding the .git file, not to the root the walk
    started from: joined at the root, ../../.git/modules/sub misses."""
    work = repo / "vendor" / "sub"
    work.mkdir(parents=True)
    (repo / ".git" / "modules").mkdir()
    git(work, "init", "-q", "-b", "main", "--separate-git-dir",
        str(repo / ".git" / "modules" / "sub"))
    (work / "in.txt").write_text("in", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "sub")
    sha = git(work, "rev-parse", "HEAD")
    # git wrote an absolute pointer and marked the file hidden, which Windows
    # refuses to overwrite in place; a submodule's own pointer is relative.
    (work / ".git").unlink()
    (work / ".git").write_text("gitdir: ../../.git/modules/sub\n", encoding="utf-8")
    deep = work / "pkg"
    deep.mkdir()

    assert head_commit(deep) == sha
    assert head_commit(deep) != git(repo, "rev-parse", "HEAD"), "not the superproject's HEAD"
    assert spawns == []


def test_a_symref_chain_falls_back_to_git(repo, spawns):
    """A ref file holding another ref is a shape the reader does not resolve; git does."""
    sha = git(repo, "rev-parse", "HEAD")
    (repo / ".git" / "refs" / "heads" / "weird").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/weird\n", encoding="utf-8")

    assert head_commit(repo) == sha
    assert spawns == [("rev-parse", "HEAD")]


def test_a_head_file_holding_nonsense_falls_back_to_git(repo, spawns):
    (repo / ".git" / "HEAD").write_text("not a ref at all\n", encoding="utf-8")

    with pytest.raises(GitError):
        head_commit(repo)
    assert spawns == [("rev-parse", "HEAD")], "git decides what an unreadable HEAD means"


def test_a_directory_that_is_not_a_repo_still_raises(tmp_path, spawns):
    with pytest.raises(GitError):
        head_commit(tmp_path)
    assert spawns == [("rev-parse", "HEAD")]
