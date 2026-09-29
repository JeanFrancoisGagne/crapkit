"""Churn counts and the recency weight, against git read another way.

The expected values come from git's numstat records (oracles/git_walk.py) with
the weights from kit.exact, or from hand_weights.tsv, worked from the logistic
the README names. crapkit is read through its CLI: the per-file map it stores
and the rows `worklist --json` prints. This file imports no crapkit module.
"""
from __future__ import annotations

import csv
from decimal import Decimal
import json
import os
from pathlib import Path

import pytest

import hang_guard
from accuracy.kit import exact, repos, rulings
from accuracy.history_oracles import churn_reads
from accuracy.history_oracles.churn_reads import Said
from accuracy.history_oracles.oracles import bugspots_runner, git_walk, pydriller_adapter
from accuracy.history_oracles.repos import history_specs as specs

HERE = Path(__file__).resolve().parent
pytestmark = pytest.mark.process


def _expected(root: Path, months: int | None) -> dict[str, Said]:
    walked = git_walk.churn(git_walk.walk(root, months))
    return {path: Said(c.commits, c.authors, c.weight) for path, c in walked.items()}


def _hand() -> dict[str, Said]:
    with (HERE / "hand_weights.tsv").open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        return {row["path"]: Said(int(row["commits"]), int(row["authors"]),
                                  Decimal(row["weight"])) for row in rows}


DORMANT = Said(0, 0, Decimal(0))  # README: a dormant file has zero churn in the window


def _rows_agree(rows: dict[str, Said], expected: dict[str, Said]) -> None:
    assert rows, "worklist --json printed no row"
    assert rows == {path: expected.get(path, DORMANT) for path in rows}


def test_hand_weights_match_the_worked_logistic(make_repo):
    built = make_repo(specs.CLOCK)

    stored, rows = churn_reads.churn(built.root, specs.CLOCK_NOW)

    assert stored == _hand()
    _rows_agree(rows, _hand())


def test_every_path_matches_the_numstat_walk(make_repo):
    built = make_repo(specs.MIXED)

    stored, rows = churn_reads.churn(built.root, specs.MIXED_NOW)

    expected = _expected(built.root, 12)
    assert specs.NON_ASCII in expected and "src/ancient.py" not in expected
    assert stored == expected
    _rows_agree(rows, expected)


@pytest.mark.parametrize("spec", [specs.ONE_COMMIT, specs.SAME_SECOND],
                         ids=["one_commit", "same_second"])
def test_single_timestamp_weighs_one(make_repo, spec):
    """README: a log whose commits share one timestamp counts each commit once."""
    built = make_repo(spec)

    stored, rows = churn_reads.churn(built.root, specs.ONE_NOW)

    assert stored == _expected(built.root, 12)
    assert all(said.weight == said.commits for said in stored.values())
    _rows_agree(rows, stored)


def test_root_below_git_top_joins(make_repo):
    built = make_repo(specs.NESTED)

    stored, rows = churn_reads.churn(built.root, specs.NESTED_NOW)

    expected = _expected(built.root, 12)
    assert set(expected) == {"crapkit.toml", "src/a.py", "src/b.py"}
    assert stored == expected
    _rows_agree(rows, expected)


def _quoted_numstat_path(root: Path, now: int) -> str:
    """The non-ASCII path as a numstat line spells it without -z."""
    out = repos.git(root, "-c", "core.quotePath=true", "log", "--since=12 months ago",
                    "--numstat", "--format=", "--", specs.NON_ASCII)
    return out.splitlines()[0].split("\t")[2]


@rulings.applies("H1")
def test_a_quoted_numstat_path_is_the_oracle_s_to_unquote(make_repo):
    built = make_repo(specs.MIXED)

    stored, _ = churn_reads.churn(built.root, specs.MIXED_NOW)

    said = next(path for path in stored if path.endswith("tf.py"))
    rulings.pin_ruling("H1", crapkit=said, oracle=_quoted_numstat_path(built.root,
                                                                      specs.MIXED_NOW))


def _literal_reading(root: Path) -> Decimal:
    """src/b.py's weight when the range runs to the newest commit git log lists
    at the root, a commit that changes nothing under it included."""
    commits = git_walk.walk(root, 12)
    oldest, _ = git_walk.span(commits)
    stamps = [commit.at for commit in commits if "src/b.py" in commit.paths]
    newest = max(commit.at for commit in commits)
    return exact.half_even(exact.churn_weight(stamps, oldest, newest), 4)


@rulings.applies("H5")
def test_the_newest_commit_with_a_file_under_the_root_anchors_the_range(make_repo):
    built = make_repo(specs.NESTED)

    stored, _ = churn_reads.churn(built.root, specs.NESTED_NOW)

    rulings.pin_ruling("H5", crapkit=stored["src/b.py"].weight,
                       oracle=_literal_reading(built.root))


def _reset(built: repos.Built, rev: str) -> None:
    repos.git(built.top, "reset", "-q", "--hard", rev)


def _head(built: repos.Built) -> str:
    return repos.git(built.top, "rev-parse", "HEAD").strip()


def test_weight_independent_of_commit_order(make_repo):
    """A merged branch's commit sits above the ones it merged over in a carried
    table and below them in git's log; the dates put src/p.py on a 4-place
    rounding edge (repos/history_specs.py ORDER_EDGE), and its window reaches
    back past 1970, so git's whole log is the window."""
    built, cold = make_repo(specs.ORDER_EDGE), make_repo(specs.ORDER_EDGE)
    merged = _head(built)
    _reset(built, "HEAD^1")
    churn_reads.churn(built.root, specs.ORDER_EDGE_NOW)
    _reset(built, merged)

    carried, _ = churn_reads.churn(built.root, specs.ORDER_EDGE_NOW)

    expected = _expected(built.root, None)
    assert expected["src/p.py"] == Said(4, 4, Decimal("0.5720"))
    assert carried == expected
    assert churn_reads.churn(cold.root, specs.ORDER_EDGE_NOW)[0] == expected


def _stored_authors(root: Path) -> list[str]:
    """The author list of the commit table crapkit stored for its next carry."""
    blob = (root / ".crapkit" / "churn-commits-v1.json").read_bytes()
    return json.loads(blob.partition(b"\n")[2])["authors"]


def test_authors_match_window_after_carry(make_repo):
    built, cold = make_repo(specs.EXPIRY), make_repo(specs.EXPIRY)
    grown = _head(built)
    _reset(built, "HEAD^1")
    churn_reads.churn(built.root, specs.EXPIRY_BEFORE)
    _reset(built, grown)

    carried, _ = churn_reads.churn(built.root, specs.EXPIRY_AFTER)

    commits = git_walk.walk(built.root, 12)
    assert specs.OLD[0] not in git_walk.window_authors(commits)
    assert sorted(_stored_authors(built.root)) == sorted(git_walk.window_authors(commits))
    assert carried == _expected(built.root, 12)
    assert churn_reads.churn(cold.root, specs.EXPIRY_AFTER)[0] == carried


# --- nightly: PyDriller and bugspots read the same histories ------------------------------


def _said_map(commits: list) -> dict[str, Said]:
    return {path: Said(c.commits, c.authors, c.weight)
            for path, c in git_walk.churn(commits).items()}


@pytest.mark.nightly
def test_pydriller_walk_matches(make_repo, oracle):
    oracle("pydriller")
    built = make_repo(specs.MIXED)

    stored, _ = churn_reads.churn(built.root, specs.MIXED_NOW)

    assert stored == _said_map(pydriller_adapter.walk(built.root, 12))


def _counts(values: dict, paths: tuple) -> str:
    return ", ".join(f"{path} {values[path]}" for path in paths if path in values)


@rulings.applies("H2")
@pytest.mark.nightly
def test_pydriller_folds_a_renamed_file_s_history(make_repo, oracle):
    oracle("pydriller")
    built = make_repo(specs.MIXED)
    names = ("src/new_name.py", "src/old_name.py")

    stored, _ = churn_reads.churn(built.root, specs.MIXED_NOW)

    folded = pydriller_adapter.commits_count(built.root, 12)
    rulings.pin_ruling("H2", crapkit=_counts({p: s.commits for p, s in stored.items()}, names),
                       oracle=_counts(folded, names))


@rulings.applies("H3")
@pytest.mark.nightly
def test_pydriller_tells_contributors_apart_by_address(make_repo, oracle):
    oracle("pydriller")
    built = make_repo(specs.MIXED)
    names = ("src/core.py", "src/util.py")

    stored, _ = churn_reads.churn(built.root, specs.MIXED_NOW)

    by_address = pydriller_adapter.contributors_count(built.root, 12)
    rulings.pin_ruling("H3", crapkit=_counts({p: s.authors for p, s in stored.items()}, names),
                       oracle=_counts(by_address, names))


@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_bugspots_scores_match_the_weights(make_repo, oracle):
    """Every commit a fix (regex /./), the clock frozen at the newest commit:
    bugspots' hotspot score is crapkit's weight."""
    oracle("bugspots")
    built = make_repo(specs.CLOCK)

    stored, _ = churn_reads.churn(built.root, specs.CLOCK_NOW)

    assert {path: said.weight for path, said in stored.items()} == bugspots_runner.scores(built.top)


def _commit_skewed(built: repos.Built) -> None:
    """src/x.py changed by a commit whose author and committer dates differ, then
    src/y.py changed at the end of the range."""
    (built.root / "src" / "x.py").write_bytes(specs.functions("x", branches=2).encode("utf-8"))
    repos.git(built.top, "add", "--", "src/x.py")
    env = {**os.environ, "GIT_AUTHOR_DATE": f"@{specs.SKEW_AUTHOR} +0000",
           "GIT_COMMITTER_DATE": f"@{specs.SKEW_COMMITTER} +0000"}
    done = hang_guard.run(["git", "commit", "-q", "-m", "rebased"], cwd=built.top, env=env)
    assert done.returncode == 0, done.stderr
    (built.root / "src" / "y.py").write_bytes(specs.functions("y", branches=2).encode("utf-8"))
    repos.git(built.top, "add", "--", "src/y.py")
    repos.git(built.top, "commit", "-q", "-m", "last", date=specs.SKEW_LAST)


@rulings.applies("H4")
@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_bugspots_dates_a_commit_by_its_committer(make_repo, oracle):
    oracle("bugspots")
    built = make_repo(specs.SKEW)
    _commit_skewed(built)

    stored, _ = churn_reads.churn(built.root, specs.SKEW_LAST + specs.DAY)

    rulings.pin_ruling("H4", crapkit=stored["src/x.py"].weight,
                       oracle=bugspots_runner.scores(built.top)["src/x.py"])


@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_a_retimed_copy_makes_bugspots_read_author_time(make_repo, oracle, tmp_path):
    """The H4 transform: with every committer date set to its author date,
    bugspots scores the rebased commit's file at crapkit's weight."""
    oracle("bugspots")
    built = make_repo(specs.SKEW)
    _commit_skewed(built)
    copy = bugspots_runner.retimed(built.top, tmp_path / "retimed")

    stored, _ = churn_reads.churn(built.root, specs.SKEW_LAST + specs.DAY)

    assert bugspots_runner.scores(copy)["src/x.py"] == stored["src/x.py"].weight


def _renamed(commits: list) -> set[str]:
    return {path for commit in commits for pair in commit.renames for path in pair if path}


@pytest.mark.nightly
@pytest.mark.platform("linux")
@pytest.mark.parametrize("name", ["MIXED", "EXPIRY"])
def test_the_window_transform_makes_bugspots_read_crapkit_s_commits(make_repo, oracle, name):
    """H16's transform: bugspots walks the window's commits, merges left out
    (H13), and every file no rename names (H15) scores at crapkit's weight."""
    oracle("bugspots")
    spec, now = getattr(specs, name), {"MIXED": specs.MIXED_NOW, "EXPIRY": specs.EXPIRY_AFTER}[name]
    built = make_repo(spec)
    commits = git_walk.walk(built.root, 12)

    stored, _ = churn_reads.churn(built.root, now)

    scored = bugspots_runner.scores(built.top, commits=bugspots_runner.window(commits))
    kept = set(stored) - _renamed(commits)
    assert kept and {path: stored[path].weight for path in kept} == {p: scored[p] for p in kept}


@rulings.applies("H16")
@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_bugspots_walks_the_whole_branch(make_repo, oracle):
    """src/a.py's first commit aged out of the 12-month window; bugspots, with no
    window, weighs it and dates its range from it."""
    oracle("bugspots")
    built = make_repo(specs.EXPIRY)

    stored, _ = churn_reads.churn(built.root, specs.EXPIRY_AFTER)

    rulings.pin_ruling("H16", crapkit=stored["src/a.py"].weight,
                       oracle=bugspots_runner.scores(built.top)["src/a.py"])


def _mixed_scores(built: repos.Built, merges: bool) -> dict[str, Decimal]:
    commits = git_walk.walk(built.root, 12)
    listed = sorted((c for c in commits if merges or c.paths), key=lambda c: -c.at)
    return bugspots_runner.scores(built.top, commits=[commit.sha for commit in listed])


@rulings.applies("H13")
@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_bugspots_counts_a_merged_change_again_at_the_merge(make_repo, oracle):
    """src/core.py changed on a branch merged back: bugspots, walking the window
    with the merge in it, weighs that change again at the merge."""
    oracle("bugspots")
    built = make_repo(specs.MIXED)

    stored, _ = churn_reads.churn(built.root, specs.MIXED_NOW)

    rulings.pin_ruling("H13", crapkit=stored["src/core.py"].weight,
                       oracle=_mixed_scores(built, merges=True)["src/core.py"])


@rulings.applies("H15")
@pytest.mark.nightly
@pytest.mark.platform("linux")
def test_bugspots_counts_a_rename_under_the_old_path_too(make_repo, oracle):
    """src/old_name.py was renamed to src/new_name.py: bugspots weighs the rename
    commit under both paths, crapkit's log under the new one only."""
    oracle("bugspots")
    built = make_repo(specs.MIXED)

    stored, _ = churn_reads.churn(built.root, specs.MIXED_NOW)

    rulings.pin_ruling("H15", crapkit=stored["src/old_name.py"].weight,
                       oracle=_mixed_scores(built, merges=False)["src/old_name.py"])
