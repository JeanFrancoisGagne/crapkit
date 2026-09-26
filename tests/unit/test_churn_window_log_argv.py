"""The churn window's git log is walked from the HEAD its caller keys on and cut
at the cutoff its caller records, always with --max-age.

The other churn tests fake _window_log whole, so they pass whatever argv it
builds. These fake one level down, at the _git_lines churn_log imports, and
read the argv the walk hands git. A walk from whatever HEAD is when git starts
counts a commit that lands during the walk twice: once in the copy keyed on
its parent, again in the next range walk. A walk cut by a second reading of
the clock can reach past the recorded cutoff at a month end. And a walk cut by
`--since="N months ago"` is cut on the local calendar, a day away from the
UTC one in some zones, so no argv here hands git `--since`, and git is never
asked for the cutoff.
"""
import pytest

from crapkit import churn_log

HEAD = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
NOW = 1790769600  # 2026-09-30T12:00:00Z
CUTOFF = 1759233600  # 12 months earlier on the UTC calendar: 2025-09-30T12:00:00Z


class FakeGit:
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def lines(self, root, *args):
        self.calls.append(args)
        return iter(())


@pytest.fixture()
def git(monkeypatch, tmp_path) -> FakeGit:
    """tmp_path holds a .git directory, as a clone's root does, so the key's
    history depth reads git's shallow list off disk and asks git nothing."""
    (tmp_path / ".git").mkdir()
    fake = FakeGit()
    churn_log._cutoff_at.cache_clear()
    monkeypatch.setattr(churn_log, "_git_lines", fake.lines)
    monkeypatch.setenv("GIT_TEST_DATE_NOW", str(NOW))
    yield fake
    churn_log._cutoff_at.cache_clear()


def walk(git: FakeGit, lines) -> tuple[str, ...]:
    """The one git call the walk made: a log, never a rev-parse first."""
    list(lines)
    (argv,) = git.calls
    return argv


def test_a_walked_window_names_its_head_and_the_cutoff_it_counted(tmp_path, git):
    window = churn_log.walked_window(tmp_path, 12, HEAD)

    assert window.cutoff == CUTOFF
    assert walk(git, window.lines) == ("log", "--relative", f"--max-age={CUTOFF}",
                                       churn_log.LOG_FORMAT, "--name-only", HEAD)


def test_a_stored_window_walks_from_the_head_its_key_names(tmp_path, git):
    argv = walk(git, churn_log.stored_window(tmp_path, 12, HEAD).lines)

    assert argv[-1] == HEAD
    assert f"--max-age={CUTOFF}" in argv


def test_a_walk_with_no_cutoff_to_record_counts_its_own(tmp_path, git):
    argv = walk(git, churn_log._window_log(tmp_path, 12, HEAD, None))

    assert f"--max-age={CUTOFF}" in argv and argv[-1] == HEAD
    assert not [arg for arg in argv if arg.startswith("--since")]


def test_a_caller_with_no_head_walks_head(tmp_path, git):
    argv = walk(git, churn_log.walked_window(tmp_path, 12, None).lines)

    assert argv[-1] == "--name-only"


@pytest.mark.parametrize("pinned", ["", "soon", "1.5"])
def test_a_clock_pin_that_is_not_a_whole_number_leaves_the_real_clock(monkeypatch, pinned):
    monkeypatch.setenv("GIT_TEST_DATE_NOW", pinned)
    monkeypatch.setattr(churn_log.time, "time", lambda: NOW + 0.75)

    assert churn_log._clock() == NOW


def test_an_unpinned_clock_is_the_real_one(monkeypatch):
    monkeypatch.delenv("GIT_TEST_DATE_NOW", raising=False)
    monkeypatch.setattr(churn_log.time, "time", lambda: NOW + 0.75)

    assert churn_log._clock() == NOW
