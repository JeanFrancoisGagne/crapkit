"""The churn window starts at one instant in every time zone.

crapkit asked git for the window's cutoff (`git rev-parse --since="N months
ago"`), and git counts "N months ago" on the local calendar: it takes the local
date, steps the month back, and lets mktime roll a day the earlier month lacks
into the next month. Where the local date and the UTC date sit on either side of
such a day, the cutoff moves by a day. At 2026-09-30T12:00Z a 6-month window
began on Mar 30 in UTC and on Mar 31 in UTC+14; at 2028-03-01T03:00Z a 12-month
window began a day later in Los Angeles and New York than in UTC. A commit in
that day counted as churn on one machine and not on the other, so a CI runner
and a laptop ranked the same repo differently.

The cutoff is now counted on the UTC calendar with git's own month arithmetic,
whatever TZ says. "Now" is pinned with GIT_TEST_DATE_NOW, the variable git's
date code reads, which the window's clock reads too.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from cli_inproc_repo import TOML, UI_TS, APP_TS, git, seed_artifacts

import pytest

from crapkit import churn_log
from crapkit.cli import main

# Every spelling the hunt varied. None leaves TZ unset: the machine's own zone.
# POSIX spellings reach git on every OS; Git for Windows reads an IANA name as
# UTC, so those two rows prove the fix only on Linux.
ZONES = [None, "UTC0", "UTC", "ABC-14", "XYZ+12", "EST5EDT", "PST8PDT", "America/Los_Angeles",
         "Asia/Tokyo", "Europe/London"]


def epoch(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp).timestamp())


class Case:
    """A pinned instant, the window, and the history around its cutoff: a commit
    that the UTC calendar keeps or drops while some zone's calendar does the
    opposite, then one well inside every window."""

    def __init__(self, months: int, now: str, cutoff: str, edge: str, recent: str, commits: int):
        self.months, self.now, self.cutoff = months, epoch(now), epoch(cutoff)
        self.edge, self.recent, self.commits = edge, recent, commits


CASES = {
    # UTC+14 is already on Oct 1: its window starts Mar 31 12:00Z, after the edge commit.
    "6 months at a month end": Case(6, "2026-09-30T12:00:00+00:00", "2026-03-30T12:00:00+00:00",
                                    "2026-03-31T00:00:00+00:00", "2026-09-01T12:00:00+00:00", 2),
    # UTC+14 is on Mar 1: its window starts Feb 28 12:00Z, before the edge commit.
    "12 months on a leap day": Case(12, "2028-02-29T12:00:00+00:00", "2027-03-01T12:00:00+00:00",
                                    "2027-02-28T18:00:00+00:00", "2027-09-01T12:00:00+00:00", 1),
    # Los Angeles and New York are still on Feb 29, which 2027 lacks: their
    # window starts Mar 2 03:00Z, after the edge commit.
    "12 months the day after a leap day": Case(12, "2028-03-01T03:00:00+00:00",
                                               "2027-03-01T03:00:00+00:00",
                                               "2027-03-01T12:00:00+00:00",
                                               "2028-02-20T00:00:00+00:00", 2),
}


def in_zone(monkeypatch, zone: str | None, now: int) -> None:
    """TZ and the pinned clock as every git child and the window will read them."""
    if zone is None:
        monkeypatch.delenv("TZ", raising=False)
    else:
        monkeypatch.setenv("TZ", zone)
    monkeypatch.setenv("GIT_TEST_DATE_NOW", str(now))
    churn_log._cutoff_at.cache_clear()


@pytest.fixture()
def bare_repo(tmp_path) -> Path:
    git(tmp_path, "init", "-q")
    return tmp_path


@pytest.mark.parametrize("zone", ZONES, ids=lambda zone: zone or "TZ-unset")
@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_the_cutoff_is_one_instant_in_every_zone(bare_repo, monkeypatch, case, zone):
    in_zone(monkeypatch, zone, case.now)

    assert churn_log._window_cutoff(bare_repo, case.months) == case.cutoff


# --- git's month arithmetic, on the UTC calendar ------------------------------

@pytest.mark.parametrize(("now", "months", "cutoff"), [
    ("2026-08-31T12:00:00+00:00", 6, "2026-03-03T12:00:00+00:00"),  # Feb 31 rolls to Mar 3
    ("2026-09-01T12:00:00+00:00", 6, "2026-03-01T12:00:00+00:00"),
    ("2028-08-31T00:00:00+00:00", 6, "2028-03-02T00:00:00+00:00"),  # a leap February: Mar 2
    ("2028-02-29T23:59:59+00:00", 12, "2027-03-01T23:59:59+00:00"),
    ("2028-02-29T23:59:59+00:00", 48, "2024-02-29T23:59:59+00:00"),
    ("2027-01-15T08:30:00+00:00", 1, "2026-12-15T08:30:00+00:00"),  # back across a year
    ("2027-03-31T00:00:00+00:00", 25, "2025-03-03T00:00:00+00:00"),
])
def test_months_count_back_on_the_utc_calendar(now, months, cutoff):
    assert churn_log._months_before(epoch(now), months) == epoch(cutoff)


def test_a_window_reaching_past_1970_starts_there():
    """git reads a negative --max-age as the far future and walks nothing."""
    now = epoch("2026-09-30T12:00:00+00:00")

    assert churn_log._months_before(now, 24300) == epoch("1970-01-30T12:00:00+00:00")
    assert churn_log._months_before(now, 10 ** 9) >= 0


def git_cutoffs(root: Path, now: int, windows: tuple[int, ...]) -> list[int]:
    """git's own cutoffs for `windows` at `now`, counted in UTC."""
    args = [f"--since={months} months ago" for months in windows]
    out = subprocess.run(["git", "rev-parse", *args], cwd=root, capture_output=True, text=True,
                         check=True, env=_utc_git_env(now)).stdout
    return [int(line.rpartition("=")[2]) for line in out.split()]


def _utc_git_env(now: int) -> dict:
    return {**os.environ, "TZ": "UTC0", "GIT_TEST_DATE_NOW": str(now)}


# The first and the last second of the last day of every month in a common year
# and a leap year, where the day-of-month overflow and the year step both happen.
FIRSTS = [datetime(year, month, 1, tzinfo=timezone.utc)
          for year in (2027, 2028, 2029) for month in range(1, 13)][1:25]
MONTH_ENDS = [first.timestamp() - offset for first in FIRSTS for offset in (86400, 1)]
WINDOWS = (1, 6, 12, 13, 25)


@pytest.mark.parametrize("now", [int(t) for t in MONTH_ENDS],
                         ids=lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%dT%H%M%S"))
def test_the_count_matches_git_reading_the_utc_calendar(bare_repo, now):
    """The oracle is git itself with TZ=UTC0: on the UTC calendar the two agree."""
    assert [churn_log._months_before(now, months) for months in WINDOWS] == \
        git_cutoffs(bare_repo, now, WINDOWS)


# --- through brief: the churn a repo reports is the same in every zone ---------

def _commit(root: Path, message: str, date: str) -> None:
    git(root, "add", "-A")
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", "commit", "-q", "-m", message],
                   cwd=root, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})


def _append(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


@pytest.fixture(scope="module", params=CASES.keys())
def dated_repo(request, tmp_path_factory) -> tuple[Case, Path]:
    """The unit suite's two-scope repo with a history dated around one case's
    cutoff, scored once so each zone's brief has a run to read."""
    case = CASES[request.param]
    root = tmp_path_factory.mktemp("dated")
    (root / "src").mkdir()
    (root / "web").mkdir()
    (root / "src" / "app.ts").write_text(APP_TS, encoding="utf-8", newline="\n")
    (root / "web" / "ui.ts").write_text(UI_TS, encoding="utf-8", newline="\n")
    (root / "crapkit.toml").write_text(
        TOML.replace("target = 6", f"target = 6\nchurn_window_months = {case.months}"),
        encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text(".crapkit/\ncoverage/\n", encoding="utf-8", newline="\n")
    git(root, "init", "-q")
    _commit(root, "init", "2025-01-01T12:00:00+00:00")
    _append(root / "src" / "app.ts", "// edge")
    _commit(root, "edge", case.edge)
    _append(root / "src" / "app.ts", "// recent")
    _commit(root, "recent", case.recent)
    seed_artifacts(root)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(root)]) == 0
    return case, root


def without_window_caches(template: Path, dest: Path) -> Path:
    """A copy of the scored repo that keeps its run and none of the laid-down
    churn or coupling copies, so brief walks git for the window again."""
    shutil.copytree(template, dest)
    for cached in (dest / ".crapkit").glob("*"):
        if cached.name.startswith(("churn", "coupling")):
            cached.unlink()
    return dest


@pytest.mark.parametrize("zone", ZONES, ids=lambda zone: zone or "TZ-unset")
def test_brief_counts_the_same_churn_in_every_zone(dated_repo, tmp_path, monkeypatch, capsys, zone):
    case, template = dated_repo
    root = without_window_caches(template, tmp_path / "repo")
    in_zone(monkeypatch, zone, case.now)
    capsys.readouterr()

    assert main(["brief", "src/app.ts", "dispatch", "--json", "--repo", str(root)]) == 0
    churn = json.loads(capsys.readouterr().out)["churn"]

    assert churn["commits"] == case.commits, churn
