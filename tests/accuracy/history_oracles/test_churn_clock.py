"""The churn window at a month end: HEAD committed on Aug 31, then on Sep 1.

The window counts back from HEAD's commit date (CONTEXT.md "Churn window"),
and git reads "6 months ago" with month arithmetic, so the cutoff moves back
at a month end: from Aug 31 it is Mar 3, from Sep 1 it is Mar 1. A commit
dated Mar 2 is out of the window at the Aug 31 HEAD and back in it at the Sep 1
HEAD. The expected values are git's own window on a clock set to HEAD's commit
date (oracles/git_walk.py); crapkit is read through its CLI, on a clock a year
past both HEADs, which moves no window. The floor test runs crapkit first at
the Aug 31 HEAD, which stores a table and a log.
"""
from __future__ import annotations

import pytest

from accuracy.kit import repos
from accuracy.history_oracles import churn_reads
from accuracy.history_oracles.churn_reads import Said
from accuracy.history_oracles.oracles import git_walk
from accuracy.history_oracles.repos import history_specs as specs

pytestmark = pytest.mark.process
A_YEAR_LATER = specs.SEP_1 + 400 * specs.DAY


def _expected(root) -> dict[str, Said]:
    walked = git_walk.churn(git_walk.walk(root, 6))
    return {path: Said(c.commits, c.authors, c.weight) for path, c in walked.items()}


def _march_2_counts(built: repos.Built) -> int:
    commits = git_walk.walk(built.root, 6)
    return sum(commit.at == specs.MARCH_2 for commit in commits)


@pytest.mark.parametrize("rev", ["HEAD^1", "HEAD"], ids=["aug31", "sep1"])
def test_a_cold_run_reads_git_s_window(make_repo, rev):
    built = make_repo(specs.MONTH_END)
    repos.git(built.top, "reset", "-q", "--hard", rev)

    stored, rows = churn_reads.churn(built.root, A_YEAR_LATER)

    assert _march_2_counts(built) == (rev == "HEAD")
    assert stored == _expected(built.root)
    assert rows == {path: _expected(built.root)[path] for path in rows}


def test_month_end_floor_matches_cold_walk(make_repo):
    """A table and a log cut at the Aug 31 HEAD, at the Mar 3 cutoff, then HEAD
    moves to the Sep 1 commit: the Mar 2 commit is back in the window (R59).
    The clock reads each HEAD's day, as it did when the window followed it."""
    built, cold = make_repo(specs.MONTH_END), make_repo(specs.MONTH_END)
    head = repos.git(built.top, "rev-parse", "HEAD").strip()
    repos.git(built.top, "reset", "-q", "--hard", "HEAD^1")
    driver = churn_reads.measure(built.root, specs.AUG_31)
    assert driver.run("coupling", "--json").code == 0  # lays the window's log down
    churn_reads.worklist_rows(driver)
    repos.git(built.top, "reset", "-q", "--hard", head)

    carried, _ = churn_reads.churn(built.root, specs.SEP_1)

    assert carried == _expected(built.root)
    assert carried["src/a.py"].commits == 2
    assert churn_reads.churn(cold.root, specs.SEP_1)[0] == carried
