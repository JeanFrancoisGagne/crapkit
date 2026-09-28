"""Churn, coupling and renames on whole histories: this repository, the synthetic
60-commit history (repos/history_specs.py SYNTHETIC), and every git bundle the
corpus holds (under tests/accuracy/corpus_goldens, and the full corpus
kit.corpus_dir finds, when there is one).

A history is cloned without a checkout and crapkit's churn, coupling and
rename readers run on it in-process, because a corpus history has no
crapkit.toml. So this file reads crapkit through its API and is not a calc's
independent test; the expected values still come from git read another way
(oracles/git_walk.py, oracles/pair_count.py) and, nightly, from PyDriller,
mlxtend, code-maat, pygit2 and bugspots. The clock is the history's own HEAD
commit time, so a history reads the same whatever the day.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from accuracy.kit import corpus_dir, repos
from accuracy.history_oracles.oracles import (bugspots_runner, code_maat, git_walk,
                                              mlxtend_adapter, pair_count, pydriller_adapter,
                                              pygit2_renames)
from accuracy.history_oracles.oracles.history_git import git, text, top_and_prefix
from accuracy.history_oracles.repos import history_specs as specs
from crapkit import churn_cache, churn_log, coupling, gitio

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
pytestmark = pytest.mark.process


def _corpus_dirs() -> list[Path]:
    return [HERE.parent / "corpus_goldens", *filter(None, [corpus_dir.locate()])]


def bundles() -> list[Path]:
    return sorted(path for base in _corpus_dirs() if base.is_dir() for path in base.rglob("*.bundle"))


@dataclass(frozen=True)
class History:
    root: Path
    now: int


def _branch(source: str) -> list[str]:
    """--branch NAME for a bundle that records no HEAD: its first branch."""
    if not source.endswith(".bundle"):
        return []
    heads = git(Path(source).parent, "bundle", "list-heads", source).decode("utf-8").split()
    return ["--branch", next(ref for ref in heads if ref.startswith("refs/heads/"))[11:]]


def _clone(source: str, dest: Path, *flags: str) -> History:
    repos.git(dest.parent, "clone", "--quiet", "--no-checkout", *flags, *_branch(source), source,
              dest.name)
    return History(dest, int(text(dest, "log", "-1", "--format=%ct")))


def _synthetic(make_repo, _tmp) -> History:
    return History(make_repo(specs.SYNTHETIC).root, specs.SYNTHETIC_NOW)


def _source(name: str):
    if name == "this_repo":  # --shared: borrow the objects, copy none
        return lambda make_repo, tmp: _clone(str(top_and_prefix(REPO)[0]), tmp / "this_repo",
                                             "--shared")
    if name == "synthetic":
        return _synthetic
    return lambda make_repo, tmp: _clone(name, tmp / Path(name).stem)


NIGHTLY = [pytest.param(name, marks=pytest.mark.nightly, id=Path(name).stem)
           for name in ["synthetic", *map(str, bundles())]]
EVERY = [pytest.param("this_repo", id="this_repo"), *NIGHTLY]


@pytest.fixture
def history(request, make_repo, tmp_path, monkeypatch) -> History:
    found = _source(request.param)(make_repo, tmp_path)
    monkeypatch.setenv("GIT_TEST_DATE_NOW", str(found.now))
    return found


def test_this_repo_is_cloned_from_the_top_of_its_checkout(tmp_path, monkeypatch):
    """mutmut runs this file from its mutants/ copy, a folder inside the stage
    checkout, which git cannot clone: the history is the checkout's."""
    head = text(REPO, "rev-parse", "HEAD")
    monkeypatch.setitem(globals(), "REPO", REPO / "tests")

    found = _source("this_repo")(None, tmp_path)

    assert text(found.root, "rev-parse", "HEAD") == head


def _walked(found: History) -> list:
    return git_walk.walk(found.root, 12, now=found.now)


def _as_tuples(table: dict) -> dict:
    return {path: (c.commits, c.authors, Decimal(repr(float(c.weight)))) for path, c in table.items()}


def _expected_churn(commits: list) -> dict:
    return {path: (c.commits, c.authors, c.weight) for path, c in git_walk.churn(commits).items()}


def tracked(found: History) -> set[str]:
    out = git(found.root, "ls-tree", "-r", "-z", "--name-only", "HEAD")
    return {path.decode("utf-8") for path in out.split(b"\0") if path}


def crapkit_pairs(found: History, paths: set[str]) -> list[tuple]:
    pairs = coupling.change_coupling_lines(churn_log.log_lines(found.root, 12), min_support=1,
                                           min_confidence=0, top=None, tracked=paths)
    return [(tuple(p["files"]), p["support"], Decimal(repr(p["confidence"]))) for p in pairs]


def _ranked(pairs: list) -> list[tuple]:
    return [(pair.files, pair.support, pair.confidence) for pair in pairs]


@pytest.mark.parametrize("history", EVERY, indirect=True)
def test_churn_matches_the_numstat_walk(history):
    said = _as_tuples(churn_cache.load_churn(history.root, 12))

    assert said == _expected_churn(_walked(history))


@pytest.mark.parametrize("history", EVERY, indirect=True)
def test_coupling_matches_the_pair_count(history):
    paths = tracked(history)

    said = crapkit_pairs(history, paths)

    expected = _ranked(pair_count.ranked(pair_count.change_sets(_walked(history)), paths))
    assert pair_count.tie_groups(said) == pair_count.tie_groups(expected)


@pytest.mark.parametrize("history", NIGHTLY, indirect=True)
def test_pydriller_walk_matches(history, oracle):
    oracle("pydriller")

    said = _as_tuples(churn_cache.load_churn(history.root, 12))

    assert said == _expected_churn(pydriller_adapter.walk(history.root, history.now, 12))


@pytest.mark.parametrize("history", NIGHTLY, indirect=True)
def test_mlxtend_pairs_match(history, oracle):
    oracle("mlxtend")
    paths = tracked(history)

    said = crapkit_pairs(history, paths)

    counted = mlxtend_adapter.counts(pair_count.change_sets(_walked(history)))
    expected = _ranked(pair_count.ranked_from(counted, paths))
    assert pair_count.tie_groups(said) == pair_count.tie_groups(expected)


@pytest.mark.platform("linux")
@pytest.mark.parametrize("history", NIGHTLY, indirect=True)
def test_code_maat_counts_match(history, oracle, tmp_path):
    oracle("code-maat")
    log = code_maat.write_log(_walked(history), tmp_path / "git2.log")

    said = churn_cache.load_churn(history.root, 12)

    revisions, authors = code_maat.revisions(log), code_maat.authors(log)
    assert {path: (c.commits, c.authors) for path, c in said.items()} == {
        path: (revisions[path], authors[path]) for path in revisions}


def _degree(support: int, revs: dict, pair: tuple[str, str]) -> int:
    """code-maat's int(100 * shared / mean(revs)), in exact arithmetic (H6)."""
    return int(Fraction(200 * support, revs[pair[0]] + revs[pair[1]]))


def _small_revisions(commits: list, log: Path) -> dict[str, int]:
    """code-maat's revisions per file over the commits of 30 files or fewer (H7)."""
    small = [commit for commit in commits if len(commit.paths) <= pair_count.BULK]
    return code_maat.revisions(code_maat.write_log(small, log))


def _within(degrees: dict, paths: set[str]) -> dict:
    """The code-maat degrees of pairs whose two files are both tracked."""
    return {pair: degree for pair, degree in degrees.items() if set(pair) <= paths}


@pytest.mark.platform("linux")
@pytest.mark.parametrize("history", NIGHTLY, indirect=True)
def test_code_maat_degree_follows_from_crapkit_s_support(history, oracle, tmp_path):
    """code-maat's coupling degree for every tracked pair, predicted from the
    support crapkit prints and the file counts of commits of 30 files or fewer
    (H6, H7)."""
    oracle("code-maat")
    commits, paths = _walked(history), tracked(history)
    revs = _small_revisions(commits, tmp_path / "small.log")

    said = crapkit_pairs(history, paths)

    degrees = code_maat.coupling(code_maat.write_log(commits, tmp_path / "all.log"))
    assert {files: _degree(support, revs, files) for files, support, _ in said} == _within(
        degrees, paths)


def _bases(found: History) -> list[str]:
    count = int(text(found.root, "rev-list", "--count", "--first-parent", "HEAD"))
    return [f"HEAD~{back}" for back in (1, 5, 20, 60) if back < count]


def _contested(found: History, base: str) -> set[str]:
    gits = git(found.root, "diff", "-M1", "--name-status", "-z", base, "HEAD")
    return pygit2_renames.contested(pygit2_renames.scores(found.root, base),
                                    pygit2_renames.git_scores(gits))


def _uncontested(renames: dict, contested: set[str]) -> dict:
    return {old: new for old, new in renames.items() if old not in contested}


@pytest.mark.parametrize("history", NIGHTLY, indirect=True)
def test_pygit2_renames_match(history, oracle):
    """Every rename libgit2 finds at 50 percent is one crapkit follows, and no
    other, except where libgit2 and git score a pair on either side of 50
    (oracles/pygit2_renames.py): those are named in the message."""
    oracle("pygit2")

    for base in _bases(history):
        said = gitio.renamed_paths(history.root, base)
        contested = _contested(history, base)
        assert _uncontested(said, contested) == _uncontested(
            pygit2_renames.renames(history.root, base), contested), (base, sorted(contested))


def _renamed(commits: list) -> set[str]:
    return {path for commit in commits for pair in commit.renames for path in pair if path}


@pytest.mark.platform("linux")
@pytest.mark.parametrize("history", NIGHTLY, indirect=True)
def test_bugspots_scores_match_away_from_renames(history, oracle, tmp_path):
    """On a copy whose committer dates are its author dates (H4), bugspots
    walking the window's commits without merges (H16, H13), every commit a fix
    and the clock at the newest commit scores every file no rename names (H15)
    at crapkit's weight."""
    oracle("bugspots")
    copy = History(bugspots_runner.retimed(history.root, tmp_path / "retimed"), history.now)
    commits = _walked(copy)

    said = churn_cache.load_churn(copy.root, 12)

    branch = text(copy.root, "branch", "--show-current")
    scores = bugspots_runner.scores(copy.root, branch, commits=bugspots_runner.window(commits))
    kept = set(said) - _renamed(commits)
    assert kept and {p: Decimal(repr(said[p].weight)) for p in kept} == {p: scores[p] for p in kept}
