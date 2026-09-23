"""The churn window's git log is walked from the HEAD its caller keys on and cut
at the cutoff its caller records.

The other churn tests fake _window_log whole, so they pass whatever argv it
builds. These fake one level down, at the _git_lines churn_log imports, and
read the argv the walk hands git. A walk from whatever HEAD is when git starts
counts a commit that lands during the walk twice: once in the copy keyed on
its parent, again in the next range walk. A walk cut by a second reading of
"N months ago" can reach past the recorded cutoff at a month end.
"""
import pytest

from crapkit import churn_log
from crapkit.errors import GitError

HEAD = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
CUTOFF = 1000000000


class FakeGit:
    def __init__(self):
        self.cutoff: int | None = CUTOFF
        self.logs: list[tuple[str, ...]] = []

    def lines(self, root, *args):
        if args[0] == "rev-parse":
            return self._rev_parse()
        self.logs.append(args)
        return iter(())

    def _rev_parse(self):
        if self.cutoff is None:
            raise GitError("git rev-parse failed")
        return iter([f"--max-age={self.cutoff}\n"])


@pytest.fixture()
def git(monkeypatch) -> FakeGit:
    fake = FakeGit()
    churn_log._cutoff_at.cache_clear()
    monkeypatch.setattr(churn_log, "_git_lines", fake.lines)
    yield fake
    churn_log._cutoff_at.cache_clear()


def walk(git: FakeGit, window: churn_log.Window) -> tuple[str, ...]:
    list(window.lines)
    (argv,) = git.logs
    return argv


def test_a_walked_window_names_its_head_and_the_cutoff_git_named(tmp_path, git):
    window = churn_log.walked_window(tmp_path, 12, HEAD)

    assert window.cutoff == CUTOFF
    assert walk(git, window) == ("log", "--relative", f"--max-age={CUTOFF}",
                                 churn_log.LOG_FORMAT, "--name-only", HEAD)


def test_a_stored_window_walks_from_the_head_its_key_names(tmp_path, git):
    argv = walk(git, churn_log.stored_window(tmp_path, 12, HEAD))

    assert argv[-1] == HEAD
    assert f"--max-age={CUTOFF}" in argv


def test_a_cutoff_git_will_not_name_is_read_off_the_clock(tmp_path, git):
    git.cutoff = None

    argv = walk(git, churn_log.walked_window(tmp_path, 12, HEAD))

    assert "--since=12 months ago" in argv and argv[-1] == HEAD


def test_a_caller_with_no_head_walks_head(tmp_path, git):
    argv = walk(git, churn_log.walked_window(tmp_path, 12, None))

    assert argv[-1] == "--name-only"
