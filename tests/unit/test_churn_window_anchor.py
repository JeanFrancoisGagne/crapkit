"""The churn window ends at HEAD's commit date, never at the wall clock.

README "Risk": the window anchors on the newest commit, so a fixed tree ranks
identically forever. The window was cut with git's `--since=12 months ago`,
which git reads against today's date, so a tree measured 400 days after its
newest commit had no churn at all and every file read dormant.

GIT_TEST_DATE_NOW is the clock git reads for `--since`: these tests move it,
on real repos with real git. Each measurement is a fresh repo, so no cache
carries an answer from one clock to the other, unless the test carries one on
purpose.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from crapkit import churn_log, coupling_cache
from crapkit.churn_cache import load_churn
from crapkit.errors import GitError
from crapkit.gitio import commit_time, head_commit, ls_files

DAY = 86_400
EPOCH = 1_750_000_000  # 2025-06-15T15:06:40Z


def git(repo: Path, *args: str, when: int | None = None, committed: int | None = None) -> None:
    env = dict(os.environ)
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f"@{when} +0000",
                   GIT_COMMITTER_DATE=f"@{when if committed is None else committed} +0000")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "core.hooksPath=.no-hooks", *args],
                   cwd=repo, env=env, check=True, capture_output=True)


def commit(repo: Path, day: int, *names: str) -> None:
    """One commit, dated `day` days after EPOCH, that edits `names`."""
    for name in names:
        (repo / name).write_text(f"day {day}\n", encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"day {day}", when=EPOCH + day * DAY)


def new_repo(repo: Path) -> Path:
    """An empty repo whose .crapkit stays out of every commit."""
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    with open(repo / ".git" / "info" / "exclude", "a", encoding="utf-8") as exclude:
        exclude.write(".crapkit/\n")
    return repo


def dated_repo(repo: Path, edits: list[tuple[int, str]]) -> Path:
    """One commit per (day after EPOCH, file) pair, dated that day."""
    new_repo(repo)
    for day, name in edits:
        commit(repo, day, name)
    return repo


def churn_at(repo: Path, monkeypatch, now: int) -> dict:
    monkeypatch.setenv("GIT_TEST_DATE_NOW", str(now))
    return dict(load_churn(repo, 12))


def test_one_commit_keeps_its_churn_400_days_later(tmp_path, monkeypatch):
    soon = churn_at(dated_repo(tmp_path / "soon", [(0, "a.py")]), monkeypatch, EPOCH + DAY)
    later = churn_at(dated_repo(tmp_path / "later", [(0, "a.py")]), monkeypatch,
                     EPOCH + 400 * DAY)

    assert list(soon) == ["a.py"]
    assert later == soon


def test_a_dated_history_weighs_the_same_a_year_after_its_newest_commit(tmp_path, monkeypatch):
    edits = [(0, "a.py"), (0, "b.py"), (10, "a.py"), (20, "b.py"), (30, "a.py")]
    soon = churn_at(dated_repo(tmp_path / "soon", edits), monkeypatch, EPOCH + 31 * DAY)
    later = churn_at(dated_repo(tmp_path / "later", edits), monkeypatch, EPOCH + 431 * DAY)

    assert {path: row.commits for path, row in soon.items()} == {"a.py": 3, "b.py": 2}
    assert later == soon


def test_the_window_still_drops_commits_a_year_older_than_head(tmp_path, monkeypatch):
    """Anchored on HEAD, the window still has an edge: a commit 400 days before
    HEAD is out of it whatever day the tree is measured on."""
    edits = [(0, "old.py"), (400, "new.py")]
    soon = churn_at(dated_repo(tmp_path / "soon", edits), monkeypatch, EPOCH + 401 * DAY)
    later = churn_at(dated_repo(tmp_path / "later", edits), monkeypatch, EPOCH + 2000 * DAY)

    assert list(soon) == ["new.py"]
    assert later == soon


def test_a_table_carried_a_year_later_answers_what_it_did_a_day_later(tmp_path, monkeypatch):
    """The stored commit table is carried to a new HEAD and expired at that
    HEAD's cutoff: the carry reads the same cutoff the walk does."""
    def carried(name: str, now: int) -> dict:
        repo = dated_repo(tmp_path / name, [(0, "a.py"), (10, "b.py")])
        churn_at(repo, monkeypatch, EPOCH + 11 * DAY)
        commit(repo, 20, "a.py")
        return churn_at(repo, monkeypatch, now)

    soon, later = carried("soon", EPOCH + 21 * DAY), carried("later", EPOCH + 420 * DAY)

    assert {path: row.commits for path, row in soon.items()} == {"a.py": 2, "b.py": 1}
    assert later == soon


def coupled_repo(repo: Path) -> Path:
    """a.py and b.py change together six times: support 6, confidence 1.0."""
    new_repo(repo)
    for day in range(6):
        commit(repo, day * 5, "a.py", "b.py")
    return repo


def pairs_at(repo: Path, monkeypatch, now: int) -> list:
    monkeypatch.setenv("GIT_TEST_DATE_NOW", str(now))
    return coupling_cache.load_coupling(repo, 12, ls_files(repo))


def test_coupling_ranks_the_same_pairs_a_year_after_the_newest_commit(tmp_path, monkeypatch):
    """Coupling, brief and worklist --batches read the laid-down log, which is
    cut at the same cutoff the churn map is."""
    soon = pairs_at(coupled_repo(tmp_path / "soon"), monkeypatch, EPOCH + 26 * DAY)
    later = pairs_at(coupled_repo(tmp_path / "later"), monkeypatch, EPOCH + 425 * DAY)

    assert [(pair["files"], pair["support"]) for pair in soon] == [(["a.py", "b.py"], 6)]
    assert later == soon
    assert (tmp_path / "later" / ".crapkit" / churn_log.LOG_NAME).is_file()


def test_a_v1_coupling_ranking_under_this_very_key_is_never_served(tmp_path, monkeypatch):
    """0.4.5 to 0.8.0 ranked pairs out of a window cut at the wall clock and
    keyed them exactly as this version does: read under its own name, an empty
    ranking written a year after the last commit would stand."""
    repo = coupled_repo(tmp_path / "repo")
    tracked = ls_files(repo)
    old = repo / ".crapkit" / coupling_cache.LEGACY_NAME
    old.parent.mkdir()
    key = coupling_cache._cache_key(repo, 12, sorted(tracked))
    old.write_text(json.dumps({"key": key, "pairs": []}), encoding="utf-8")

    assert [pair["support"] for pair in pairs_at(repo, monkeypatch, EPOCH + 425 * DAY)] == [6]
    assert not old.exists(), "nothing reads the v1 name again"
    assert coupling_cache.CACHE_NAME != coupling_cache.LEGACY_NAME


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize(("stamp", "months", "cutoff"), [
    ("2025-08-31T10:20:30", 6, "2025-03-03T10:20:30"),  # Feb 31 runs on into March
    ("2025-09-01T10:20:30", 6, "2025-03-01T10:20:30"),
    ("2024-03-31T00:00:00", 1, "2024-03-02T00:00:00"),  # a leap February has 29 days
    ("2024-02-29T23:59:59", 12, "2023-03-01T23:59:59"),
    ("2026-01-15T08:00:00", 1, "2025-12-15T08:00:00"),  # back across a year
    ("2026-01-15T08:00:00", 25, "2023-12-15T08:00:00"),
])
def test_months_before_counts_calendar_months_the_way_git_does(stamp, months, cutoff):
    assert churn_log.months_before(utc(stamp), months) == utc(cutoff)


def test_months_before_1970_is_the_epoch():
    assert churn_log.months_before(utc("1971-06-01T00:00:00"), 30) == 0


def civil(year: int, month: int, day: int, seconds: int = 0) -> int:
    """Unix seconds at a UTC date in the proleptic Gregorian calendar, for any
    year datetime cannot hold: days counted from the leap-year rule itself."""
    y = year - (month <= 2)
    era, yoe = divmod(y, 400)
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return (era * 146_097 + doe - 719_468) * DAY + seconds


def test_the_civil_oracle_agrees_with_datetime_where_both_reach():
    for text in ("1970-01-01", "2000-02-29", "2025-08-31", "2100-03-01", "9999-12-31"):
        year, month, day = map(int, text.split("-"))
        assert civil(year, month, day, 3_723) == utc(f"{text}T01:02:03")


@pytest.mark.parametrize(("stamp", "months", "cutoff"), [
    # 12000 is a leap year (divisible by 400): Feb 29 12000 exists.
    (civil(12000, 6, 1), 12, civil(11999, 6, 1)),
    (civil(12000, 3, 31, 45), 1, civil(12000, 3, 2, 45)),  # Feb 31 runs on 2 days
    (civil(12100, 3, 31), 1, civil(12100, 3, 3)),  # 12100 is not: Feb 31 runs on 3
    (civil(10000, 1, 15), 1, civil(9999, 12, 15)),  # back across datetime's last year
    (civil(221_818_234, 8, 31, 7), 6, civil(221_818_234, 3, 3, 7)),
    (civil(2425, 7, 15), 4_801, civil(2025, 6, 15)),  # 400 years and one month
    (civil(9_000, 1, 1), 3_000_000_000, 0),  # 250 million years back is before 1970
])
def test_months_before_counts_exactly_past_year_9999(stamp, months, cutoff):
    """A commit dated past datetime's year 9999 counts back on the same
    calendar; clamped to 9999-12-31, its window swallowed every commit
    between that date and HEAD, thousands of years of them."""
    assert churn_log.months_before(stamp, months) == cutoff


def commit_at(repo: Path, when: int, *names: str) -> None:
    """One commit dated `when` that edits `names`."""
    for name in names:
        (repo / name).write_text(f"at {when}\n", encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"at {when}", when=when)


def history_at(repo: Path, edits: list[tuple[int, str]]) -> dict:
    """The churn of one commit per (Unix seconds, file) pair, oldest first."""
    new_repo(repo)
    for when, name in edits:
        commit_at(repo, when, name)
    return {path: (row.commits, row.weight) for path, row in load_churn(repo, 12).items()}


def test_a_history_committed_after_2038_weighs_what_it_did_in_2024(tmp_path):
    """git 2.43 for Windows reads `--max-age` as a 32-bit int, so a cutoff past
    2038-01-19 wrapped negative and the window listed nothing: a tree whose
    HEAD a skewed clock dated 2040 read every file dormant. The same history
    sixteen years earlier, another leap year, is the oracle: same spacing to
    the second, same churn."""
    def spaced(year: int) -> list[tuple[int, str]]:
        return [(civil(year, 1, 1), "a.py"), (civil(year, 1, 1, 60), "b.py"),
                (civil(year, 3, 1), "a.py"), (civil(year, 6, 1), "b.py")]

    in_2024 = history_at(tmp_path / "y2024", spaced(2024))
    in_2040 = history_at(tmp_path / "y2040", spaced(2040))

    assert {path: commits for path, (commits, _) in in_2024.items()} == {"a.py": 2, "b.py": 2}
    assert in_2040 == in_2024


def test_a_head_past_year_9999_keeps_a_twelve_month_window(tmp_path):
    """HEAD at 12000-06-01: the window opens at 11999-06-01, to the second."""
    churn = history_at(tmp_path / "repo", [
        (civil(11999, 3, 1), "old.py"),
        (civil(11999, 5, 31, DAY - 1), "just_out.py"),
        (civil(11999, 6, 1), "edge.py"),
        (civil(11999, 9, 1), "a.py"),
        (civil(12000, 6, 1), "head.py"),
    ])

    assert sorted(churn) == ["a.py", "edge.py", "head.py"]


def test_commit_time_reads_the_commit_date_not_the_author_date(tmp_path):
    """A rebased commit carries two dates, and the window filters on the
    commit date, as git's `--since` does."""
    repo = new_repo(tmp_path / "repo")
    (repo / "a.py").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "rebased", when=EPOCH, committed=EPOCH + 90 * DAY)

    assert commit_time(repo, head_commit(repo)) == EPOCH + 90 * DAY


def test_commit_time_of_an_unknown_commit_is_a_git_error(tmp_path):
    repo = dated_repo(tmp_path / "repo", [(0, "a.py")])

    with pytest.raises(GitError):
        commit_time(repo, "0" * 40)
