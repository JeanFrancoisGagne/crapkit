"""merge-base and ancestry against an arbitrary commit, not just HEAD.

`verify --base REF` needs both: the commit a branch actually forked from, and
whether a recorded run sits at or behind it. Real git in tmp_path — the answer
these two give decides what a verify measures, so a stub would prove nothing.
"""
import subprocess
from pathlib import Path

import pytest

from crapkit.cli.verifying import _require_ancestor
from crapkit.errors import GitError
from crapkit.gitio import GitFacts, is_ancestor, merge_base
from translated_git import speak_french


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def forked(tmp_path: Path) -> tuple[Path, str, str, str]:
    """main at C1; a feature branch two commits past it, checked out."""
    git(tmp_path, "init", "-q", "-b", "main")
    base = commit(tmp_path, "one")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    mid = commit(tmp_path, "two")
    head = commit(tmp_path, "three")
    return tmp_path, base, mid, head


def test_merge_base_is_the_fork_point_not_head(forked):
    repo, base, _, head = forked

    assert merge_base(repo, "main") == base
    assert merge_base(repo, "main") != head


def test_a_commit_is_its_own_ancestor(forked):
    repo, base, _, _ = forked

    assert is_ancestor(repo, base, base) is True


def test_a_branch_commit_is_not_behind_the_fork_point(forked):
    repo, base, mid, _ = forked

    assert is_ancestor(repo, mid, base) is False
    assert is_ancestor(repo, base, mid) is True


def test_ancestry_still_defaults_to_head(forked):
    repo, base, mid, _ = forked

    assert is_ancestor(repo, base) is True
    assert is_ancestor(repo, mid) is True


def test_a_commit_this_clone_does_not_hold_is_not_behind_head(forked):
    repo, _, _, _ = forked

    assert is_ancestor(repo, "0" * 40) is False


def test_a_failed_ancestry_read_is_no_answer(forked):
    """git exits 128 for a commit it lacks and for a read that failed. The
    second is no answer: read as "no", verify blamed a rewritten history and
    next-item named changed files on a tree nobody touched."""
    repo, base, _, _ = forked

    with pytest.raises(GitError, match="merge-base --is-ancestor"):
        is_ancestor(repo, base, "refs/crapkit/no-such-ref")


def _lose(repo: Path, sha: str) -> None:
    """Delete one commit's loose object, as a failed disk or an interrupted
    copy can: git holds the commits on both sides and cannot read this one."""
    loose = repo / ".git" / "objects" / sha[:2] / sha[2:]
    loose.chmod(0o644)
    loose.unlink()


def test_an_unreadable_commit_on_the_way_is_no_answer(forked):
    """git walks from HEAD back to the commit it is asked about. When a commit
    on that walk cannot be read, `merge-base --is-ancestor` exits 1, the exit
    it gives for "no", and prints `error: Could not read <sha>`. That read is
    no answer: taken as "no", next-item said the stamp commit was not behind
    HEAD on a tree whose history still held it."""
    repo, base, mid, _ = forked
    _lose(repo, mid)

    with pytest.raises(GitError, match=mid):
        is_ancestor(repo, base)


def test_verify_names_the_failed_read_not_a_rewritten_history(forked):
    """The baseline check read the same failure as a rebase or an amend and
    asked for a fresh baseline that the next run would refuse the same way."""
    repo, base, mid, _ = forked
    _lose(repo, mid)

    with pytest.raises(GitError) as refused:
        _require_ancestor(GitFacts(repo), base)

    assert mid in str(refused.value)
    assert "rewrote history" not in str(refused.value)


@pytest.mark.parametrize("language", ["git's own", "French"])
def test_an_unreadable_commit_is_no_answer_in_any_language(forked, language, tmp_path_factory,
                                                           monkeypatch):
    """git translates the `error:` prefix that marks the failed read (`erreur
    :` in French). crapkit asks git for its untranslated words, so an
    unreadable commit stays a failed read on a git that speaks the user's
    language, and no read of it says "no"."""
    repo, base, mid, _ = forked
    if language == "French":
        speak_french(tmp_path_factory.mktemp("catalog"), monkeypatch)
    _lose(repo, mid)

    with pytest.raises(GitError, match=mid):
        is_ancestor(repo, base)
    with pytest.raises(GitError, match=mid):
        merge_base(repo, "main")


def test_trace_output_on_a_plain_no_is_still_no(forked, monkeypatch):
    """GIT_TRACE=1 makes git print trace lines on stderr for every command, a
    plain "no" included. Only an `error:` or `fatal:` line marks a failed
    read: taken as one, verify answered an amended history with git's trace
    text instead of naming the rewrite."""
    repo, base, mid, head = forked
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--amend", "-m", "amended")
    monkeypatch.setenv("GIT_TRACE", "1")

    assert is_ancestor(repo, mid, base) is False
    with pytest.raises(GitError, match="rebase or amend rewrote history"):
        _require_ancestor(GitFacts(repo), head)


@pytest.fixture()
def unrelated(tmp_path: Path) -> Path:
    """HEAD on an orphan branch that shares no commit with main."""
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "one")
    git(tmp_path, "checkout", "-q", "--orphan", "other")
    commit(tmp_path, "two")
    return tmp_path


def test_an_unrelated_history_has_no_merge_base(unrelated):
    with pytest.raises(GitError) as refused:
        merge_base(unrelated, "main")

    assert str(refused.value) == f"no merge base between main and HEAD in {unrelated}"


def test_trace_output_on_no_merge_base_is_still_no_merge_base(unrelated, monkeypatch):
    """merge-base exits 1 both for two histories that share no commit and for
    an unreadable one; only the second prints an `error:` line."""
    monkeypatch.setenv("GIT_TRACE", "1")

    with pytest.raises(GitError) as refused:
        merge_base(unrelated, "main")

    assert str(refused.value) == f"no merge base between main and HEAD in {unrelated}"
