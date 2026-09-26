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


def test_a_commit_dated_past_year_9999_anchors_on_its_last_second():
    assert churn_log.months_before(10 ** 15, 12) == utc("9998-12-31T23:59:59")


def test_commit_time_reads_the_commit_date_not_the_author_date(tmp_path):
    """A rebased commit carries two dates, and the window filters on the
    commit date, as `--max-age` does."""
    repo = new_repo(tmp_path / "repo")
    (repo / "a.py").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "rebased", when=EPOCH, committed=EPOCH + 90 * DAY)

    assert commit_time(repo, head_commit(repo)) == EPOCH + 90 * DAY


def test_commit_time_of_an_unknown_commit_is_a_git_error(tmp_path):
    repo = dated_repo(tmp_path / "repo", [(0, "a.py")])

    with pytest.raises(GitError):
        commit_time(repo, "0" * 40)
