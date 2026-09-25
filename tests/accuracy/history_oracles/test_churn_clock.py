"""The churn window on git's clock: GIT_TEST_DATE_NOW at Aug 31 and at Sep 1.

git reads "6 months ago" with month arithmetic, so the window's cutoff moves
back at a month end: from Aug 31 it is Mar 3, from Sep 1 it is Mar 1. A
commit dated Mar 2 leaves the window on Aug 31 and is back in it on Sep 1.
The expected values are git's own window (oracles/git_walk.py); crapkit is
read through its CLI, after a run on Aug 31 that stored a table and a log.
"""
from __future__ import annotations

import pytest

from accuracy.kit import repos
from accuracy.history_oracles import churn_reads
from accuracy.history_oracles.churn_reads import Said
from accuracy.history_oracles.oracles import git_walk
from accuracy.history_oracles.repos import history_specs as specs

pytestmark = pytest.mark.process


def _expected(root, now: int) -> dict[str, Said]:
    walked = git_walk.churn(git_walk.walk(root, 6, now=now))
    return {path: Said(c.commits, c.authors, c.weight) for path, c in walked.items()}


def _march_2_counts(now: int, built: repos.Built) -> int:
    commits = git_walk.walk(built.root, 6, now=now)
    return sum(commit.at == specs.MARCH_2 for commit in commits)


@pytest.mark.parametrize("now", [specs.AUG_31, specs.SEP_1], ids=["aug31", "sep1"])
def test_a_cold_run_reads_git_s_window(make_repo, now):
    built = make_repo(specs.MONTH_END)

    stored, rows = churn_reads.churn(built.root, now)

    assert _march_2_counts(now, built) == (now == specs.SEP_1)
    assert stored == _expected(built.root, now)
    assert rows == {path: _expected(built.root, now)[path] for path in rows}


def test_month_end_floor_matches_cold_walk(make_repo):
    """A table and a log cut on Aug 31 at the Mar 3 cutoff, then HEAD moves and
    the clock reads Sep 1: the Mar 2 commit is back in the window (R59)."""
    built, cold = make_repo(specs.MONTH_END), make_repo(specs.MONTH_END)
    head = repos.git(built.top, "rev-parse", "HEAD").strip()
    repos.git(built.top, "reset", "-q", "--hard", "HEAD^1")
    driver = churn_reads.measure(built.root, specs.AUG_31)
    assert driver.run("coupling", "--json").code == 0  # lays the window's log down
    churn_reads.worklist_rows(driver)
    repos.git(built.top, "reset", "-q", "--hard", head)

    carried, _ = churn_reads.churn(built.root, specs.SEP_1)

    assert carried == _expected(built.root, specs.SEP_1)
    assert carried["src/a.py"].commits == 2
    assert churn_reads.churn(cold.root, specs.SEP_1)[0] == carried
