"""ChangeReads: git's answers about paths under a stamp, started together."""
import subprocess
from pathlib import Path

import pytest

from crapkit import gitio, lane_changes
from crapkit.errors import GitError
from crapkit.lane_changes import ChangeReads, visible_paths
from hang_guard import HANG_SECONDS
from translated_git import speak_french


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=HANG_SECONDS, check=True).stdout


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


@pytest.mark.parametrize("language", ["git's own", "French"])
def test_an_unreadable_commit_since_the_stamp_is_no_answer(repo, language, tmp_path_factory,
                                                           monkeypatch):
    """`merge-base --is-ancestor` exits 1, its "no", and prints `error: Could
    not read <sha>` when a commit between the stamp and HEAD is unreadable. A
    French git prints `erreur :`, so the read asks for git's own words."""
    root, first = repo
    if language == "French":
        speak_french(tmp_path_factory.mktemp("catalog"), monkeypatch)
    _write(root, "docs/n.md", "two\n")
    lost = _commit(root, "two")
    _write(root, "docs/n.md", "three\n")
    _commit(root, "three")
    loose = root / ".git" / "objects" / lost[:2] / lost[2:]
    loose.chmod(0o644)
    loose.unlink()

    with ChangeReads(root, (first,), ("src",)) as reads, pytest.raises(GitError, match=lost):
        reads.is_ancestor(first)


def test_trace_output_on_a_plain_no_is_still_no(repo, monkeypatch):
    """GIT_TRACE=1 puts trace lines on stderr beside a plain "no". Read as a
    failed read, next-item said git could not tell which files changed where
    the stamp commit had left history."""
    root, first = repo
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--amend", "-m", "amended")
    monkeypatch.setenv("GIT_TRACE", "1")

    with ChangeReads(root, (first,), ("src",)) as reads:
        assert reads.is_ancestor(first) is False


def test_a_commit_this_clone_does_not_hold_is_not_behind_head(repo):
    root, _ = repo
    with ChangeReads(root, ("0" * 40,), ("src",)) as reads:
        assert reads.is_ancestor("0" * 40) is False


def test_a_failed_ancestry_read_is_no_answer(repo, monkeypatch):
    """merge-base exits 128 on a commit this clone holds: git could not read
    the repository, which answers nothing about ancestry."""
    root, first = repo
    real = lane_changes._start
    failing = ("rev-parse", "--verify", "refs/crapkit/no-such-ref")
    monkeypatch.setattr(lane_changes, "_start", lambda r, *args: real(
        r, *(failing if args[0] == "merge-base" else args)))

    with ChangeReads(root, (first,), ("src",)) as reads:
        with pytest.raises(GitError, match="no-such-ref"):
            reads.is_ancestor(first)


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


def test_only_the_ancestry_read_asks_for_git_s_own_words(repo, monkeypatch):
    """The French test above skips on a machine with no locale that loads a
    catalog, so the locale each read runs under is also read where it is set."""
    root, first = repo
    started = []
    real = gitio.start_read
    monkeypatch.setattr(gitio, "start_read", lambda r, *args, pinned=(): (
        started.append(("merge-base" in args, args[0], pinned)), real(r, *args, pinned=pinned))[1])

    with ChangeReads(root, (first,), ("src",)) as reads:
        reads.changed_since(first)

    assert set(started) == {(True, "--literal-pathspecs", gitio.UNTRANSLATED),
                            (False, "--literal-pathspecs", ())}
    # merge-base, the diff since the commit, the flag listing, rev-parse and status
    assert len(started) == 5


def test_each_commit_gets_its_own_ancestry_answer(repo):
    root, first = repo
    with ChangeReads(root, (first, "0" * 40), ("src",)) as reads:
        assert (reads.is_ancestor(first), reads.is_ancestor("0" * 40)) == (True, False)


def test_visible_paths_reads_each_path_as_a_path(repo):
    """`src/[id]` as a glob also matches the file `src/d`; `-draft` read as an
    option is an unknown switch; and a file under no path is not listed."""
    root, _ = repo
    for rel in ("src/[id]/a.py", "src/d", "-draft/c.py"):
        _write(root, rel)

    assert visible_paths(root, ("src/[id]", "-draft")) == ("-draft/c.py", "src/[id]/a.py")
