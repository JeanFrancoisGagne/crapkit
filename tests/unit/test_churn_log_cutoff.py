"""A laid-down churn log is re-dated only back to the cutoff it was cut at.

git reads "N months ago" with month arithmetic, so the window cutoff can
move back overnight: 6 months before Aug 31 is Mar 3, and before Sep 1 it is Mar 1.
A log cut at the later cutoff lacks the commits between the two, and re-dating
it cannot bring them back. So the log's key records the cutoff its walk was cut
at, the walk is cut at exactly that cutoff (`--max-age`), and a refresh
below it walks the window instead.

Every git seam is monkeypatched; walks are counted, never timed.
"""
import json

import pytest

from crapkit import churn_log

HEAD_A = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
HEAD_B = "bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222"


def block(author: str, ct: int, *paths: str) -> list[str]:
    """One commit in the stored shape, %an %at %ct, authored when committed."""
    return [f"\x01{author}\x02{ct}\x02{ct}\n"] + [f"{p}\n" for p in paths]


NEW = block("carol", 1000009000, "src/c.py")
MID = block("bob", 1000000500, "src/a.py")
OLD = block("alice", 1000000000, "src/a.py", "src/b.py")
LOG = MID + OLD


def since(lines: list[str], cutoff: int | None) -> list[str]:
    kept, keep = [], True
    for line in lines:
        if line.startswith("\x01"):
            keep = cutoff is None or int(line.rstrip("\n").rpartition("\x02")[2]) >= cutoff
        if keep:
            kept.append(line)
    return kept


class FakeGit:
    def __init__(self):
        self.head = HEAD_A
        self.log = list(LOG)
        self.ranges: dict[tuple[str, str], list[str]] = {}
        self.cutoff: int | None = 999999999
        self.walked_at: list[int | None] = []  # the cutoff each window walk was cut at
        self.range_calls: list[tuple[str, str]] = []

    def window(self, root, months, head, cutoff):
        self.walked_at.append(cutoff)
        return iter(since(self.log, self.cutoff if cutoff is None else cutoff))

    def range(self, root, base, head):
        self.range_calls.append((base, head))
        return iter(self.ranges.get((base, head), []))


@pytest.fixture()
def git(monkeypatch) -> FakeGit:
    fake = FakeGit()
    monkeypatch.setattr(churn_log, "_window_log", fake.window)
    monkeypatch.setattr(churn_log, "_range_log", fake.range)
    monkeypatch.setattr(churn_log, "_window_cutoff", lambda root, months: fake.cutoff)
    monkeypatch.setattr(churn_log, "is_ancestor", lambda root, commit, other: True)
    monkeypatch.setattr(churn_log, "head_commit", lambda root: fake.head)
    return fake


def new_day(monkeypatch, day: str) -> None:
    monkeypatch.setattr(churn_log, "_utc_date", lambda: day)


def stamp(root) -> dict:
    return json.loads(churn_log._key_path(root / ".crapkit" / churn_log.LOG_NAME)
                      .read_text(encoding="utf-8"))


def stored(root) -> list[str]:
    return list(churn_log.stored_window(root, 12, None).lines)


def test_the_walk_is_cut_at_the_cutoff_the_stamp_records(tmp_path, git):
    git.cutoff = 1000000100

    window = churn_log.stored_window(tmp_path, 12, None)

    assert list(window.lines) == MID, "alice's commit sits below the cutoff"
    assert window.cutoff == 1000000100
    assert git.walked_at == [1000000100], "walked at the cutoff read, not at a second reading"
    assert stamp(tmp_path)["cutoff"] == 1000000100


def test_a_cutoff_moved_back_walks_the_window_again(tmp_path, git, monkeypatch):
    new_day(monkeypatch, "2026-08-31")
    git.cutoff = 1000000100
    stored(tmp_path)
    new_day(monkeypatch, "2026-09-01")
    git.cutoff = 1000000000

    assert stored(tmp_path) == LOG, "the cold walk at the earlier cutoff has alice's commit"
    assert git.walked_at == [1000000100, 1000000000]
    assert stamp(tmp_path)["cutoff"] == 1000000000


def test_a_cutoff_moved_forward_re_dates_the_log_without_a_walk(tmp_path, git, monkeypatch):
    new_day(monkeypatch, "2026-08-21")
    stored(tmp_path)
    new_day(monkeypatch, "2026-08-22")
    git.cutoff = 1000000100

    window = churn_log.stored_window(tmp_path, 12, None)

    assert list(window.lines) == MID
    assert window.cutoff == 1000000100
    assert len(git.walked_at) == 1


def test_a_log_that_recorded_no_cutoff_is_walked_not_refreshed(tmp_path, git):
    """A log laid down before the cutoff was recorded: nothing says how far
    back it reaches, so carrying it forward could keep a gap."""
    stored(tmp_path)
    doc = stamp(tmp_path)
    del doc["cutoff"]
    churn_log._key_path(tmp_path / ".crapkit" / churn_log.LOG_NAME).write_text(
        json.dumps(doc), encoding="utf-8")
    git.head = HEAD_B
    git.ranges[(HEAD_A, HEAD_B)] = list(NEW)
    git.log = NEW + LOG

    assert stored(tmp_path) == NEW + LOG
    assert len(git.walked_at) == 2
    assert git.range_calls == []


def test_a_served_log_that_recorded_no_cutoff_names_none(tmp_path, git):
    stored(tmp_path)
    doc = stamp(tmp_path)
    doc["cutoff"] = "not a cutoff"
    churn_log._key_path(tmp_path / ".crapkit" / churn_log.LOG_NAME).write_text(
        json.dumps(doc), encoding="utf-8")

    window = churn_log.stored_window(tmp_path, 12, None)

    assert list(window.lines) == LOG, "the exact key still serves the log"
    assert window.cutoff is None, "but no table may be stamped from it"
    assert len(git.walked_at) == 1


def test_a_cutoff_git_will_not_name_walks_by_the_clock(tmp_path, git):
    stored(tmp_path)
    git.head = HEAD_B
    git.ranges[(HEAD_A, HEAD_B)] = list(NEW)
    git.log = NEW + LOG
    git.cutoff = None

    window = churn_log.stored_window(tmp_path, 12, None)

    assert list(window.lines) == NEW + LOG
    assert window.cutoff is None
    assert git.walked_at[-1] is None, "no cutoff to cut at: git reads --since itself"
    assert git.range_calls == []
