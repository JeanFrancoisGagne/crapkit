"""A churn map miss at a moved HEAD folds in only the new commits.

The per-file map is keyed on HEAD, so the first churn read after any commit
missed it and parsed the whole window again: 635,761 log lines on a large
consumer repo, when a handful were new. The window's commits are now kept as a
table (author, author date, commit date per commit; commits per path), and a
miss at a HEAD that grew from the table walks only `stored..HEAD`.

Every git seam is monkeypatched: a carry is proved by the window walk not
firing and the range walk firing instead, and a skipped log read by the log
never being inflated. Expected maps are worked by hand from the recency
logistic: 1/(1+e^(12-12t)) at position t between the oldest and newest author
date, so 0.5 at the newest, 1/(1+e^6) = 0.0024726 halfway, 1/(1+e^3) = 0.0474259
at three quarters, and 1/(1+e^12) = 0.0000061 at the oldest.
"""
import json

import pytest

from crapkit import churn_cache, churn_commits, churn_log
from crapkit.churn import FileChurn
from crapkit.errors import GitError

HEAD_0 = "0000aaaa0000aaaa0000aaaa0000aaaa0000aaaa"
HEAD_A = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
HEAD_B = "bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222"
FLOOR = 999999999  # a window floor every commit here clears


def block(author: str, at: int, ct: int, *paths: str) -> list[str]:
    """One commit as git prints it under --format=%x01%an%x02%at%x02%ct --name-only."""
    return [f"\x01{author}\x02{at}\x02{ct}\n"] + [f"{p}\n" for p in paths]


OLD = block("alice", 1000000000, 1000000000, "src/a.py", "src/b.py")
MID = block("bob", 1000000500, 1000000500, "src/a.py")
NEW = block("carol", 1000001000, 1000001000, "src/c.py", "src/a.py")
NEW_ALICE = block("alice", 1000000750, 1000000750, "src/b.py")

LOG = MID + OLD  # git log is newest first
RANGE = NEW + NEW_ALICE

AT_A = {"src/a.py": FileChurn(2, 2, 0.5), "src/b.py": FileChurn(1, 1, 0.0)}
# a.py: carol at the newest (0.5), bob halfway (0.0024726), alice at the oldest.
# b.py: alice twice, one author; three quarters (0.0474259) plus the oldest.
AT_B = {"src/a.py": FileChurn(3, 3, 0.5025), "src/b.py": FileChurn(2, 1, 0.0474),
        "src/c.py": FileChurn(1, 1, 0.5)}


def since(lines: list[str], floor: int | None) -> list[str]:
    """The commit blocks committed at or after `floor`; all of them when git
    names no floor."""
    kept, keep = [], True
    for line in lines:
        if line.startswith("\x01"):
            keep = floor is None or int(line.rstrip("\n").rpartition("\x02")[2]) >= floor
        if keep:
            kept.append(line)
    return kept


class FakeGit:
    """Counts the walks the table is supposed to save."""

    def __init__(self):
        self.head = HEAD_A
        self.log = list(LOG)
        self.logs: dict[str, list[str]] = {}  # the window at a named HEAD, when it differs
        self.ranges: dict[tuple[str, str], list[str]] = {}
        self.floor: int | None = FLOOR
        self.ancestor = True
        self.shallow = False
        self.window_calls = 0
        self.range_calls: list[tuple[str, str]] = []
        self.cutoff_calls = 0
        self.ancestry_calls = 0
        self.inflates = 0
        self.after_read = None  # a commit that lands right after the next HEAD read
        self.floor_after_walk: int | None = None  # the clock crosses a month end mid-read

    def read_head(self, root):
        head = self.head
        if self.after_read is not None:
            land, self.after_read = self.after_read, None
            land()
        return head

    def window(self, root, months, head=None, *rest):
        """The window at `head` (the current HEAD's when no head is named),
        cut at the floor as git's --since would cut it."""
        self.window_calls += 1
        walked = since(self.logs.get(head, self.log), self.floor)
        if self.floor_after_walk is not None:
            self.floor, self.floor_after_walk = self.floor_after_walk, None
        return iter(walked)

    def range(self, root, base, head):
        self.range_calls.append((base, head))
        return iter(self.ranges.get((base, head), []))

    def cutoff(self, root, months):
        self.cutoff_calls += 1
        return self.floor

    def is_ancestor(self, root, commit, other):
        self.ancestry_calls += 1
        return self.ancestor


@pytest.fixture()
def git(monkeypatch) -> FakeGit:
    fake = FakeGit()
    monkeypatch.setattr(churn_log, "_window_log", fake.window)
    monkeypatch.setattr(churn_log, "_range_log", fake.range)
    monkeypatch.setattr(churn_log, "_window_cutoff", fake.cutoff)
    monkeypatch.setattr(churn_log, "is_ancestor", fake.is_ancestor)
    monkeypatch.setattr(churn_log, "head_commit", fake.read_head)
    monkeypatch.setattr(churn_commits, "is_shallow", lambda root: fake.shallow)
    monkeypatch.setattr(churn_cache, "head_commit", fake.read_head)
    inflate = churn_log._inflate

    def counted(blob):
        fake.inflates += 1
        return inflate(blob)

    monkeypatch.setattr(churn_log, "_inflate", counted)
    return fake


def table_file(root):
    return root / ".crapkit" / churn_commits.COMMITS_NAME


def dump(churn: dict) -> str:
    """The map as the cache file writes it: sorted, floats in repr."""
    return json.dumps({p: list(c) for p, c in sorted(churn.items())})


def move_head(git: FakeGit) -> None:
    git.head = HEAD_B
    git.ranges[(HEAD_A, HEAD_B)] = list(RANGE)


def new_day(monkeypatch, day: str) -> None:
    """A new UTC day for the map and the laid-down log alike."""
    monkeypatch.setattr(churn_cache, "_utc_date", lambda: day)
    monkeypatch.setattr(churn_log, "_utc_date", lambda: day)


def test_a_moved_head_folds_in_only_the_new_commits(tmp_path, git):
    assert churn_cache.load_churn(tmp_path, 12) == AT_A
    move_head(git)

    assert churn_cache.load_churn(tmp_path, 12) == AT_B
    assert git.window_calls == 1, "two new commits must not cost the whole window"
    assert git.range_calls == [(HEAD_A, HEAD_B)]


# A merged branch. rita committed before sue and sam but was merged after them,
# so the range walk carries her commit above theirs while git's own log lists it
# below. The dates span 7*10^15 seconds, which steps the recency weights finely
# enough to land src/p.py on a 4-decimal rounding edge: its weights are
# 0.003149365420286381 (rita), 0.5 (sam) and 0.07380063457971359 (sue). Summed
# rita first they give 0.5769500000000001, which rounds to 0.577; in log order,
# 0.57695, which rounds to 0.5769. The exact sum is 3.2e-17 below 0.57695, so
# 0.5769 is the answer in either order.
SAM = block("sam", 7000001000000000, 7000001000000000, "src/p.py")
SUE = block("sue", 5524329729096004, 5524329729096004, "src/p.py")
RITA = block("rita", 3641517686962032, 3641517686962032, "src/p.py")
OSCAR = block("oscar", 1000000000, 1000000000, "src/z.py")
MERGE = ["\x01sam\x027000001000000001\x027000001000000001\n"]  # --name-only lists no path


def test_a_carried_map_is_byte_identical_to_a_cold_rebuild(tmp_path, git):
    git.log = SAM + SUE + OSCAR
    churn_cache.load_churn(tmp_path, 12)
    git.head = HEAD_B
    git.ranges[(HEAD_A, HEAD_B)] = MERGE + RITA
    carried = churn_cache.load_churn(tmp_path, 12)

    for stale in (tmp_path / ".crapkit").iterdir():
        stale.unlink()
    git.log = MERGE + SAM + SUE + RITA + OSCAR
    cold = churn_cache.load_churn(tmp_path, 12)

    assert git.window_calls == 2
    assert carried["src/p.py"] == FileChurn(3, 3, 0.5769)
    assert dump(carried) == dump(cold)


def test_the_carried_table_is_carried_again(tmp_path, git):
    churn_cache.load_churn(tmp_path, 12)
    move_head(git)
    churn_cache.load_churn(tmp_path, 12)
    git.head = "cccc3333cccc3333cccc3333cccc3333cccc3333"
    git.ranges[(HEAD_B, git.head)] = block("bob", 1000001000, 1000001000, "src/c.py")

    churn = churn_cache.load_churn(tmp_path, 12)

    assert git.window_calls == 1
    assert git.range_calls[-1] == (HEAD_B, git.head)
    # c.py: carol and bob, both at the newest author date.
    assert churn["src/c.py"] == FileChurn(2, 2, 1.0)


def lay_log_behind(tmp_path, git: FakeGit) -> None:
    """A log laid down at HEAD_0, one commit behind HEAD_A, by a command that
    needs per-commit structure."""
    git.head, git.log = HEAD_0, list(OLD)
    list(churn_log.log_lines(tmp_path, 12))
    git.head, git.log = HEAD_A, list(LOG)
    git.ranges[(HEAD_0, HEAD_A)] = list(MID)
    git.ranges[(HEAD_0, HEAD_B)] = RANGE + MID


def land_after_the_head_read(git: FakeGit) -> None:
    """HEAD_B's two commits land between load_churn's HEAD read and its walk."""
    def land():
        git.logs[HEAD_A] = list(LOG)
        move_head(git)
        git.log = RANGE + LOG
    git.after_read = land


@pytest.mark.parametrize("laid", [False, True], ids=["walked", "through-the-log"])
def test_a_commit_landing_mid_read_is_counted_once(tmp_path, git, laid):
    """Another session commits between load_churn's HEAD read and the window
    walk. The table is stored under the HEAD that was read, so it must hold that
    HEAD's commits and no more: the next miss carries stored..HEAD, and a commit
    already in the table would count twice there and at every carry after."""
    if laid:
        lay_log_behind(tmp_path, git)
    land_after_the_head_read(git)

    assert churn_cache.load_churn(tmp_path, 12) == AT_A, "the map answers the HEAD it read"
    assert git.head == HEAD_B, "the commit landed mid-read"
    assert churn_cache.load_churn(tmp_path, 12) == AT_B


def test_a_carry_and_the_log_refresh_after_it_ask_git_once(tmp_path, git):
    """brief and worklist --batches read the map and then the coupling log at
    one HEAD. Both bring their copy forward over the same two commits, so the
    ancestry answer, the window floor and the range walk are each asked of git
    once, not once per copy."""
    list(churn_log.log_lines(tmp_path, 12))
    churn_cache.load_churn(tmp_path, 12)
    move_head(git)
    git.cutoff_calls = git.ancestry_calls = 0

    assert churn_cache.load_churn(tmp_path, 12) == AT_B
    list(churn_log.log_lines(tmp_path, 12))

    assert (git.ancestry_calls, git.cutoff_calls) == (1, 1)
    assert git.range_calls == [(HEAD_A, HEAD_B)]
    assert git.window_calls == 1


def test_a_moved_head_leaves_the_stored_log_unread(tmp_path, git):
    """A command that needs per-commit structure laid the log down. The map's
    miss must not re-stream it: the table already holds what the map needs."""
    list(churn_log.log_lines(tmp_path, 12))
    churn_cache.load_churn(tmp_path, 12)
    log = tmp_path / ".crapkit" / churn_log.LOG_NAME
    laid = log.read_bytes()
    move_head(git)
    git.inflates = 0

    assert churn_cache.load_churn(tmp_path, 12) == AT_B
    assert git.inflates == 0, "the table answered; the log was not read"
    assert log.read_bytes() == laid


def test_a_new_day_expires_on_the_commit_date_not_the_author_date(tmp_path, git, monkeypatch):
    """dave's commit was rebased: authored long ago, committed recently, so git's
    --since keeps it. erin's is the other way round, and git drops it."""
    git.log = (block("dave", 1000000000, 1000009000, "src/d.py")
               + block("erin", 1000008000, 1000000100, "src/e.py"))
    new_day(monkeypatch, "2026-08-21")
    churn_cache.load_churn(tmp_path, 12)
    new_day(monkeypatch, "2026-08-22")
    git.floor = 1000005000

    carried = churn_cache.load_churn(tmp_path, 12)

    # One author date left is no range: dave's commit counts once.
    assert carried == {"src/d.py": FileChurn(1, 1, 1.0)}
    assert (git.window_calls, git.range_calls) == (1, []), "same HEAD: nothing to walk"


def test_a_head_the_table_is_not_behind_rebuilds_in_full(tmp_path, git):
    """A rewritten history shares no commits with the stored one: folding the
    range onto it would keep commits HEAD no longer has."""
    churn_cache.load_churn(tmp_path, 12)
    git.head = HEAD_B
    git.ancestor = False
    git.log = list(NEW)

    assert churn_cache.load_churn(tmp_path, 12) == {"src/c.py": FileChurn(1, 1, 1.0),
                                                     "src/a.py": FileChurn(1, 1, 1.0)}
    assert git.window_calls == 2
    assert git.range_calls == []


def test_a_cutoff_behind_the_stored_one_rebuilds_in_full(tmp_path, git, monkeypatch):
    """git's month arithmetic moves the floor back at a month end: 6 months
    before Aug 31 reads Mar 3, before Sep 1 reads Mar 1. The window widens past
    commits the table dropped, so only a walk has them."""
    git.floor = 1000000100
    churn_cache.load_churn(tmp_path, 12)
    new_day(monkeypatch, "2099-01-01")
    git.floor = 1000000000

    assert churn_cache.load_churn(tmp_path, 12) == AT_A
    assert git.window_calls == 2


def test_a_cutoff_behind_the_stored_one_walks_past_a_laid_log(tmp_path, git, monkeypatch):
    """The same month end with a log on disk. The log was cut at the higher
    floor too, so re-dating it at the lower one still lacks alice's commit:
    the rebuild has to walk the window, not refresh the log."""
    git.floor = 1000000100
    list(churn_log.log_lines(tmp_path, 12))
    churn_cache.load_churn(tmp_path, 12)
    new_day(monkeypatch, "2099-01-01")
    git.floor = 1000000000

    assert churn_cache.load_churn(tmp_path, 12) == AT_A
    assert git.window_calls == 2


def test_the_table_records_the_floor_its_walk_was_cut_at(tmp_path, git, monkeypatch):
    """The floor is read again after the walk and has moved back meanwhile, a
    month end crossed mid-read. A table stamped with that later, lower floor
    claims alice's commit is in it when the walk cut it out, and the next
    carry would never bring it back."""
    git.floor = 1000000100
    git.floor_after_walk = 1000000000
    churn_cache.load_churn(tmp_path, 12)
    new_day(monkeypatch, "2099-01-01")

    assert churn_cache.load_churn(tmp_path, 12) == AT_A
    assert git.window_calls == 2


def test_a_cutoff_git_will_not_name_rebuilds_in_full(tmp_path, git):
    churn_cache.load_churn(tmp_path, 12)
    move_head(git)
    git.floor = None
    git.log = RANGE + LOG

    assert churn_cache.load_churn(tmp_path, 12) == AT_B
    assert git.window_calls == 2
    assert git.range_calls == []


def test_a_parse_without_a_cutoff_keeps_no_table(tmp_path, git):
    git.floor = None
    churn_cache.load_churn(tmp_path, 12)

    assert not table_file(tmp_path).exists()


def test_a_different_window_rebuilds_in_full(tmp_path, git):
    churn_cache.load_churn(tmp_path, 12)
    churn_cache.load_churn(tmp_path, 3)

    assert git.window_calls == 2, "--since=3 months is a different question"
    assert git.range_calls == []


def test_a_table_of_another_path_format_rebuilds_in_full(tmp_path, git, monkeypatch):
    churn_cache.load_churn(tmp_path, 12)
    line, _, body = table_file(tmp_path).read_bytes().partition(b"\n")
    key = json.loads(line)
    key["paths"] = "root-relative"
    table_file(tmp_path).write_bytes(json.dumps(key).encode("utf-8") + b"\n" + body)
    new_day(monkeypatch, "2099-01-01")

    assert churn_cache.load_churn(tmp_path, 12) == AT_A
    assert git.window_calls == 2


@pytest.mark.parametrize("damage", [
    lambda blob: blob[:-10],                             # torn
    lambda blob: blob[:-3] + b"xyz",                     # corrupted body
    lambda blob: b"{not json\n" + blob.partition(b"\n")[2],  # unreadable key
    lambda blob: b'{"months": 12}\n' + blob.partition(b"\n")[2],  # key missing fields
    lambda blob: b"",                                    # empty
])
def test_a_damaged_table_reads_as_cold(tmp_path, git, monkeypatch, damage):
    churn_cache.load_churn(tmp_path, 12)
    table_file(tmp_path).write_bytes(damage(table_file(tmp_path).read_bytes()))
    new_day(monkeypatch, "2099-01-01")

    assert churn_cache.load_churn(tmp_path, 12) == AT_A
    assert git.window_calls == 2, "damage is a miss, never a crash"


def test_a_table_body_of_another_shape_reads_as_cold(tmp_path, git, monkeypatch):
    """Intact bytes, wrong shape: what another version's writer could leave."""
    import zlib

    churn_cache.load_churn(tmp_path, 12)
    line, _, _ = table_file(tmp_path).read_bytes().partition(b"\n")
    key = json.loads(line)
    body = b'{"commits": [[1, 2]], "authors": [], "files": {}}'
    key.update(size=len(body), crc=zlib.crc32(body))
    table_file(tmp_path).write_bytes(json.dumps(key).encode("utf-8") + b"\n" + body)
    new_day(monkeypatch, "2099-01-01")

    assert churn_cache.load_churn(tmp_path, 12) == AT_A
    assert git.window_calls == 2


def test_an_undated_log_keeps_no_table(tmp_path, git):
    """Without commit dates nothing in the table could ever expire."""
    git.log = ["\x01bob\x021000000500\n", "src/a.py\n"]
    churn_cache.load_churn(tmp_path, 12)

    assert not table_file(tmp_path).exists()


def test_an_unreadable_head_keeps_no_table(tmp_path, git, monkeypatch):
    def no_head(root):
        raise GitError("no HEAD commit")

    monkeypatch.setattr(churn_cache, "head_commit", no_head)

    assert churn_cache.load_churn(tmp_path, 12) == AT_A
    assert not table_file(tmp_path).exists()


def test_a_read_only_crapkit_still_answers(tmp_path, git, monkeypatch):
    def refuse(self, data):
        raise PermissionError("read-only")

    monkeypatch.setattr(type(tmp_path), "write_bytes", refuse)

    assert churn_cache.load_churn(tmp_path, 12) == AT_A
    assert not table_file(tmp_path).exists()


def test_a_shallow_clone_keeps_no_table(tmp_path, git):
    """Deepening a shallow clone adds history under an unmoved HEAD; a carried
    table would never see it, a walk does."""
    git.shallow = True
    churn_cache.load_churn(tmp_path, 12)

    assert not table_file(tmp_path).exists()


def test_a_clone_git_cannot_vouch_for_keeps_no_table(tmp_path, git, monkeypatch):
    def unanswerable(root):
        raise GitError("rev-parse failed")

    monkeypatch.setattr(churn_commits, "is_shallow", unanswerable)
    churn_cache.load_churn(tmp_path, 12)

    assert not table_file(tmp_path).exists()


def test_a_path_keeps_the_commits_that_did_not_age_out(tmp_path, git, monkeypatch):
    new_day(monkeypatch, "2026-08-21")
    churn_cache.load_churn(tmp_path, 12)
    new_day(monkeypatch, "2026-08-22")
    git.floor = 1000000200  # alice's commit aged out; bob's did not

    # a.py keeps bob's commit, alone in the window now; b.py had only alice's.
    assert churn_cache.load_churn(tmp_path, 12) == {"src/a.py": FileChurn(1, 1, 1.0)}
    assert git.window_calls == 1


def test_an_empty_window_carries_the_commits_that_arrive(tmp_path, git):
    git.log = []
    assert churn_cache.load_churn(tmp_path, 12) == {}
    move_head(git)

    # carol is the newest author date (0.5) and alice's new commit the oldest.
    assert churn_cache.load_churn(tmp_path, 12) == {
        "src/c.py": FileChurn(1, 1, 0.5), "src/a.py": FileChurn(1, 1, 0.5),
        "src/b.py": FileChurn(1, 1, 0.0)}
    assert git.window_calls == 1
