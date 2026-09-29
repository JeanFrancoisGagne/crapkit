"""The churn window's git log is walked from the HEAD its caller keys on and cut
at the cutoff its caller records.

The other churn tests fake _window_log whole, so they pass whatever argv it
builds. These fake one level down, at the _git_lines churn_log imports, and
read the argv the walk hands git. A walk from whatever HEAD is when git starts
counts a commit that lands during the walk twice: once in the copy keyed on
its parent, again in the next range walk. A walk cut by `--since=N months ago`
reads git's clock: a tree measured a year after its last commit walked nothing.
"""
import pytest

from crapkit import churn_log
from crapkit.errors import GitError

HEAD = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
CUTOFF = 1000000000


class FakeGit:
    """HEAD's commit date is named as the cutoff itself; months_before has
    tests of its own."""

    def __init__(self):
        self.cutoff: int | None = CUTOFF
        self.logs: list[tuple[str, ...]] = []

    def lines(self, root, *args):
        self.logs.append(args)
        return iter(())

    def commit_date(self, root, commit):
        if self.cutoff is None:
            raise GitError(f"git rev-list named no commit date for {commit}")
        return self.cutoff


@pytest.fixture()
def git(monkeypatch, tmp_path) -> FakeGit:
    """tmp_path holds a .git directory, as a clone's root does, so the key's
    history depth reads git's shallow list off disk and asks git nothing."""
    (tmp_path / ".git").mkdir()
    fake = FakeGit()
    churn_log._commit_date.cache_clear()
    monkeypatch.setattr(churn_log, "_git_lines", fake.lines)
    monkeypatch.setattr(churn_log, "commit_time", fake.commit_date)
    monkeypatch.setattr(churn_log, "months_before", lambda stamp, months: stamp)
    monkeypatch.setattr(churn_log, "head_commit", lambda root: HEAD)
    yield fake
    churn_log._commit_date.cache_clear()


def walk(git: FakeGit, window: churn_log.Window) -> tuple[str, ...]:
    list(window.lines)
    (argv,) = git.logs
    return argv


def test_a_walked_window_names_its_head_and_the_cutoff_its_date_names(tmp_path, git):
    window = churn_log.walked_window(tmp_path, 12, HEAD)

    assert window.cutoff == CUTOFF
    assert walk(git, window) == ("log", "--relative", f"--since=@{CUTOFF} +0000",
                                 churn_log.LOG_FORMAT, "--encoding=UTF-8", "--name-only", HEAD)


def test_a_stored_window_walks_from_the_head_its_key_names(tmp_path, git):
    argv = walk(git, churn_log.stored_window(tmp_path, 12, HEAD))

    assert argv[-1] == HEAD
    assert f"--since=@{CUTOFF} +0000" in argv


def test_no_walk_ever_reads_the_clock(tmp_path, git):
    """`@<seconds> +0000` is an exact second; any other --since, such as
    `12 months ago`, reads git's clock."""
    for head in (HEAD, None):
        list(churn_log.walked_window(tmp_path, 12, head).lines)

    cuts = [arg for argv in git.logs for arg in argv if arg.startswith(("--since", "--max-age"))]
    assert cuts == [f"--since=@{CUTOFF} +0000"] * 2


def test_a_cutoff_past_2038_reaches_git_digit_for_digit(tmp_path, git):
    """git 2.43 for Windows reads `--max-age` as a 32-bit int and wrapped any
    cutoff past 2038-01-19; the absolute --since carries every digit."""
    git.cutoff = 7_000_001_000_000_000

    argv = walk(git, churn_log.walked_window(tmp_path, 12, HEAD))

    assert "--since=@7000001000000000 +0000" in argv
    assert not any(arg.startswith("--max-age") for arg in argv)


def test_a_head_with_no_commit_date_walks_nothing(tmp_path, git):
    """No date, no window to cut: git is never asked for a log, where the walk
    used to fall back on `--since=12 months ago` and git's clock."""
    git.cutoff = None

    window = churn_log.walked_window(tmp_path, 12, HEAD)

    assert (list(window.lines), window.cutoff, git.logs) == ([], None, [])


def test_a_caller_with_no_head_walks_head(tmp_path, git):
    argv = walk(git, churn_log.walked_window(tmp_path, 12, None))

    assert argv[-1] == "--name-only"
