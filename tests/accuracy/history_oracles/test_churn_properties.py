"""Churn at the parser: crapkit's reading of a log against a clean-room model of
the same commits, and the relations its numbers keep.

The log is the text git prints for crapkit's churn format (a header of author
name, author date and commit date, then the paths). The model is
oracles/git_walk.churn over the same commits, weights from kit.exact. Commit
times come from kit.strategies.stamps, which draws the equal-timestamp (R94)
and month-end (R59) shapes among the rest.
"""
from __future__ import annotations

from decimal import Decimal
import random

from hypothesis import given, strategies as st

from accuracy.kit import strategies
from accuracy.kit.settings import pure
from accuracy.history_oracles.oracles import git_walk
from crapkit import churn

PATHS = ("src/a.py", "src/b.py", "src/ünicode.py", "docs/with space.md", "src/c.py")
AUTHORS = ("A U Thor", "Bea Ruiz", "Chen Li")


def _commits(stamps) -> list[git_walk.Commit]:
    """Commits newest first, as git logs them; stamps arrive parent first."""
    made = [git_walk.Commit(f"c{i}", AUTHORS[(stamp.author + i) % 3], "", stamp.author,
                            stamp.committer,
                            tuple(sorted({PATHS[i % 5], PATHS[(i * 3 + 1) % 5]})))
            for i, stamp in enumerate(stamps)]
    return made[::-1]


def _log(commits: list[git_walk.Commit]) -> str:
    blocks = (f"\x01{c.author}\x02{c.at}\x02{c.ct}\n\n" + "".join(p + "\n" for p in c.paths)
              for c in commits)
    return "\n".join(blocks)


def _said(table: dict) -> dict:
    return {path: (c.commits, c.authors, Decimal(repr(c.weight))) for path, c in table.items()}


def _model(commits) -> dict:
    return {path: (c.commits, c.authors, c.weight)
            for path, c in git_walk.churn(commits).items()}


@strategies.examples("stamps")
@given(strategies.stamps())
@pure
def test_the_parser_matches_the_model(stamps):
    commits = _commits(stamps)

    assert _said(churn.parse_git_log(_log(commits))) == _model(commits)


@strategies.examples("stamps")
@given(strategies.stamps())
@pure
def test_weight_stays_under_half_per_commit_unless_one_timestamp(stamps):
    """README: each commit weighs at most 0.5, the newest; one timestamp weighs 1."""
    table = churn.parse_git_log(_log(_commits(stamps)))
    one_time = len({stamp.author for stamp in stamps}) == 1

    for said in table.values():
        assert said.authors <= said.commits
        assert said.weight == said.commits if one_time else said.weight <= 0.5 * said.commits


@strategies.examples("stamps")
@given(strategies.stamps())
@pure
def test_authors_shrink_with_the_window(stamps):
    """Dropping the oldest commit, as a narrower window does, raises no count."""
    commits = _commits(stamps)
    wide = churn.parse_git_log(_log(commits))
    narrow = churn.parse_git_log(_log(commits[:-1]))

    for path, said in narrow.items():
        assert said.commits <= wide[path].commits
        assert said.authors <= wide[path].authors


@given(strategies.stamps(), st.randoms(use_true_random=False))
@pure
def test_commit_order_moves_no_number(stamps, rng: random.Random):
    """The same commits listed in another order, as a carried table lists a
    merged branch (R57), give the same map."""
    commits = _commits(stamps)
    shuffled = rng.sample(commits, len(commits))

    assert churn.parse_git_log(_log(shuffled)) == churn.parse_git_log(_log(commits))


def _split(stamps) -> tuple[list, list]:
    commits = _commits(stamps)
    cut = len(commits) // 2
    return commits[cut:], commits[:cut]


@strategies.examples("stamps")
@given(strategies.stamps())
@pure
def test_a_carried_table_answers_a_cold_fold(stamps):
    """The newer commits folded in on top of a table of the older ones give the
    map and the author list a cold parse of all of them gives (R103)."""
    older, newer = _split(stamps)
    table = churn.fold(_log(older).split("\n"))
    table.carry(churn.fold(_log(newer).split("\n")))
    cold = churn.fold(_log(newer + older).split("\n"))

    assert table.churn() == cold.churn()
    assert sorted(table.authors) == sorted(cold.authors)


@strategies.examples("stamps")
@given(strategies.stamps())
@pure
def test_expiry_answers_a_fold_of_the_survivors(stamps):
    """Expiring at a cutoff keeps the commits whose commit date reaches it, and
    only the authors those commits carry (R103)."""
    commits = _commits(stamps)
    cutoff = sorted(commit.ct for commit in commits)[len(commits) // 2]
    table = churn.fold(_log(commits).split("\n"))
    table.expire(cutoff)
    survivors = [commit for commit in commits if commit.ct >= cutoff]

    assert _said(table.churn()) == _model(survivors)
    assert sorted(table.authors) == sorted(git_walk.window_authors(survivors))
