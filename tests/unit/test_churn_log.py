"""The churn LOG cache: the raw per-commit stream, kept on disk deflated.

churn-cache.json stores the derived per-file map. Coupling needs the structure
that map threw away — which files shared a commit — and re-walked twelve months
of history for it on every brief and every `worklist --batches`. This is the
same walk, written down.

Every git seam is monkeypatched, so a served cache is proved by the window walk
never firing, and a refresh by the range walk firing instead of it. No test here
times anything.
"""
import json
import zlib

import pytest

from crapkit import churn_log
from crapkit.errors import GitError

HEAD_A = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
HEAD_B = "bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222"


def block(author: str, at: int, ct: int, *paths: str) -> list[str]:
    """One commit as git prints it under --format=%x01%an%x02%at%x02%ct --name-only."""
    return [f"\x01{author}\x02{at}\x02{ct}\n"] + [f"{p}\n" for p in paths]


OLD = block("alice", 1000000000, 1000000000, "src/a.py", "src/b.py")
MID = block("bob", 1000000500, 1000000500, "src/a.py")
NEW = block("carol", 1000009000, 1000009000, "src/c.py")

LOG = MID + OLD  # git log is newest first
FLOOR = 999999999  # a window floor that keeps every commit above

SHIPPED = ["\x01bob\x021000000500\n", "src/a.py\n",
           "\x01alice\x021000000000\n", "src/a.py\n", "src/b.py\n"]
SHIPPED_REFRESHED = ["\x01carol\x021000009000\n", "src/c.py\n"] + SHIPPED


class FakeGit:
    """Counts the walks the cache is supposed to save."""

    def __init__(self):
        self.head = HEAD_A
        self.log = list(LOG)
        self.ranges: dict[tuple[str, str], list[str]] = {}
        self.floor = FLOOR
        self.ancestor = True
        self.window_calls = 0
        self.range_calls: list[tuple[str, str]] = []
        self.cutoff_calls = 0

    def window(self, root, months):
        self.window_calls += 1
        return iter(self.log)

    def range(self, root, base, head):
        self.range_calls.append((base, head))
        return iter(self.ranges.get((base, head), []))

    def cutoff(self, root, months):
        self.cutoff_calls += 1
        return self.floor

    def is_ancestor(self, root, commit, other):
        return self.ancestor


@pytest.fixture()
def git(monkeypatch) -> FakeGit:
    fake = FakeGit()
    monkeypatch.setattr(churn_log, "_window_log", fake.window)
    monkeypatch.setattr(churn_log, "_range_log", fake.range)
    monkeypatch.setattr(churn_log, "_window_cutoff", fake.cutoff)
    monkeypatch.setattr(churn_log, "is_ancestor", fake.is_ancestor)
    monkeypatch.setattr(churn_log, "head_commit", lambda root: fake.head)
    return fake


def cache(tmp_path):
    return tmp_path / ".crapkit" / "churn-log.z"


def test_a_second_read_at_the_same_head_never_walks_the_window(tmp_path, git):
    cold = list(churn_log.log_lines(tmp_path, 12))
    warm = list(churn_log.log_lines(tmp_path, 12))

    assert git.window_calls == 1, "the whole point: an unmoved HEAD walks git once"
    assert warm == cold
    assert cache(tmp_path).is_file()


def test_the_served_lines_carry_no_commit_date(tmp_path, git):
    """Consumers parse %an\\x02%at. The commit date is the cache's own bookkeeping
    and must not reach them, or every churn key and every coupling pair shifts."""
    assert list(churn_log.log_lines(tmp_path, 12)) == SHIPPED
    assert list(churn_log.log_lines(tmp_path, 12)) == SHIPPED


def test_the_cache_file_is_deflate_and_keeps_the_commit_date(tmp_path, git):
    list(churn_log.log_lines(tmp_path, 12))

    raw = zlib.decompress(cache(tmp_path).read_bytes()).decode("utf-8")
    assert raw == "".join(LOG), "the stored log is the enriched one, verbatim"


def test_a_torn_cache_reads_as_cold(tmp_path, git):
    list(churn_log.log_lines(tmp_path, 12))
    cache(tmp_path).write_bytes(cache(tmp_path).read_bytes()[:-4])

    assert list(churn_log.log_lines(tmp_path, 12)) == SHIPPED
    assert git.window_calls == 2, "a truncated log is a miss, never a crash"


def test_a_reader_that_stops_early_leaves_no_cache(tmp_path, git):
    lines = churn_log.log_lines(tmp_path, 12)
    next(lines)
    lines.close()

    assert not cache(tmp_path).exists(), "a half-written log must never look valid"
    assert list(p.name for p in (tmp_path / ".crapkit").glob("*.part")) == []
    assert list(churn_log.log_lines(tmp_path, 12)) == SHIPPED


def test_a_different_window_rebuilds(tmp_path, git):
    list(churn_log.log_lines(tmp_path, 12))
    list(churn_log.log_lines(tmp_path, 3))

    assert git.window_calls == 2, "--since=3 months is a different question"
    assert git.range_calls == []


def test_an_unreadable_head_still_answers_and_writes_nothing(tmp_path, git, monkeypatch):
    def no_head(root):
        raise GitError("no HEAD commit")

    monkeypatch.setattr(churn_log, "head_commit", no_head)

    assert list(churn_log.log_lines(tmp_path, 12)) == SHIPPED
    assert not cache(tmp_path).exists(), "nothing safe to key on means nothing to cache"


def test_a_moved_head_walks_only_the_new_commits(tmp_path, git):
    list(churn_log.log_lines(tmp_path, 12))
    git.head = HEAD_B
    git.ranges[(HEAD_A, HEAD_B)] = list(NEW)

    assert list(churn_log.log_lines(tmp_path, 12)) == SHIPPED_REFRESHED
    assert git.window_calls == 1, "one commit must not cost twelve months of walking"
    assert git.range_calls == [(HEAD_A, HEAD_B)]


def test_a_refresh_matches_the_full_rebuild_it_replaces(tmp_path, git):
    list(churn_log.log_lines(tmp_path, 12))
    git.head = HEAD_B
    git.ranges[(HEAD_A, HEAD_B)] = list(NEW)
    refreshed = list(churn_log.log_lines(tmp_path, 12))

    for stale in (tmp_path / ".crapkit").iterdir():
        stale.unlink()
    git.log = list(NEW + LOG)

    assert list(churn_log.log_lines(tmp_path, 12)) == refreshed


def test_the_refreshed_cache_is_served_at_the_new_head(tmp_path, git):
    list(churn_log.log_lines(tmp_path, 12))
    git.head = HEAD_B
    git.ranges[(HEAD_A, HEAD_B)] = list(NEW)
    list(churn_log.log_lines(tmp_path, 12))
    git.range_calls.clear()

    assert list(churn_log.log_lines(tmp_path, 12)) == SHIPPED_REFRESHED
    assert (git.window_calls, git.range_calls) == (1, []), "a refresh must land on disk"


def test_a_new_utc_day_costs_no_walk_at_all(tmp_path, git, monkeypatch):
    """`--since=N months ago` is wall-clock relative, so a day-old log describes a
    day-wide window. Re-dating it is arithmetic on stored commit dates, not a walk."""
    monkeypatch.setattr(churn_log, "_utc_date", lambda: "2026-08-21")
    list(churn_log.log_lines(tmp_path, 12))
    monkeypatch.setattr(churn_log, "_utc_date", lambda: "2026-08-22")

    assert list(churn_log.log_lines(tmp_path, 12)) == SHIPPED
    assert (git.window_calls, git.range_calls) == (1, []), "same HEAD: no range to walk"


def test_commits_below_the_window_floor_drop_out_on_a_refresh(tmp_path, git, monkeypatch):
    monkeypatch.setattr(churn_log, "_utc_date", lambda: "2026-08-21")
    list(churn_log.log_lines(tmp_path, 12))
    monkeypatch.setattr(churn_log, "_utc_date", lambda: "2026-08-22")
    git.floor = 1000000100  # alice's commit has aged out of the window

    assert list(churn_log.log_lines(tmp_path, 12)) == SHIPPED[:2]
    assert git.window_calls == 1


def test_a_head_the_cache_is_not_behind_rebuilds(tmp_path, git):
    """A rewritten or rewound history shares no line with the cached one: its
    commits are not in HEAD, and prepending to them would invent churn."""
    list(churn_log.log_lines(tmp_path, 12))
    git.head = HEAD_B
    git.ancestor = False
    git.log = list(NEW)

    assert list(churn_log.log_lines(tmp_path, 12)) == ["\x01carol\x021000009000\n",
                                                       "src/c.py\n"]
    assert git.window_calls == 2
    assert git.range_calls == []


def test_the_key_file_records_what_it_keys_on(tmp_path, git):
    list(churn_log.log_lines(tmp_path, 12))
    doc = json.loads((tmp_path / ".crapkit" / "churn-log.json").read_text(encoding="utf-8"))

    assert doc["head"] == HEAD_A
    assert doc["months"] == 12
    assert doc["size"] == cache(tmp_path).stat().st_size
