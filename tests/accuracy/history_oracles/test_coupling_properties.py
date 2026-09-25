"""Change coupling at the parser: crapkit's ranking of a log against the pair count
in oracles/pair_count.py over the same commits, and the relations it keeps.

The log is the text git prints for crapkit's churn format, each path spelled
the way git spells it with core.quotePath off: a path holding a double quote, a
backslash or a control character is C-quoted (git-config core.quotePath), and
any other path, non-ASCII included, is printed as it is. Paths come from
kit.strategies.path_text, which draws the tab-in-a-path shape of R76.
"""
from __future__ import annotations

from decimal import Decimal
import random

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import repos, strategies
from accuracy.kit.settings import pure
from accuracy.history_oracles.oracles import pair_count
from accuracy.history_oracles.repos import history_specs as specs
from crapkit import coupling, coupling_cache

_ESCAPES = {7: "a", 8: "b", 9: "t", 10: "n", 11: "v", 12: "f", 13: "r", 34: '"', 92: "\\"}


def _char(char: str) -> str:
    code = ord(char)
    if code in _ESCAPES:
        return "\\" + _ESCAPES[code]
    return f"\\{code:03o}" if code < 32 or code == 127 else char


def git_spelling(path: str) -> str:
    """How git prints `path` in a log with core.quotePath=false: a character over
    0x7f is printed as it is, so only the ASCII ones decide and get escaped."""
    if all(_char(char) == char for char in path):
        return path
    return '"' + "".join(_char(char) for char in path) + '"'


def _log(change_sets: list[tuple]) -> str:
    return "\n".join("\x01author\x02100\x02100\n\n" + "".join(git_spelling(p) + "\n" for p in files)
                     for files in change_sets)


@st.composite
def histories(draw):
    pool = draw(st.lists(strategies.path_text(), min_size=2, max_size=6, unique=True))
    small = st.lists(st.sampled_from(pool), min_size=1, max_size=len(pool), unique=True)
    commits = draw(st.lists(small, min_size=1, max_size=12))
    if draw(st.booleans()):
        commits.append(pool + [f"bulk/{i}.py" for i in range(pair_count.BULK + 1 - len(pool))])
    return [tuple(commit) for commit in commits]


def _said(pairs: list[dict]) -> list[tuple]:
    return [(tuple(p["files"]), p["support"], Decimal(repr(p["confidence"]))) for p in pairs]


def _model(change_sets: list[tuple], tracked: set[str]) -> list[tuple]:
    pairs = pair_count.ranked([frozenset(c) for c in change_sets], tracked)
    return [(pair.files, pair.support, pair.confidence) for pair in pairs]


@given(histories())
@pure
def test_the_ranking_matches_the_pair_count(change_sets):
    tracked = {path for commit in change_sets for path in commit}

    got = coupling.change_coupling(_log(change_sets), min_support=1, min_confidence=0,
                                   top=None, tracked=tracked)

    assert _said(got) == _model(change_sets, tracked)


@given(histories())
@pure
def test_support_and_confidence_stay_in_bounds(change_sets):
    files, _ = pair_count.counts([frozenset(c) for c in change_sets])

    got = coupling.change_coupling(_log(change_sets), min_support=1, min_confidence=0, top=None)

    for pair in got:
        assert pair["support"] <= min(files[path] for path in pair["files"])
        assert 0 < pair["confidence"] <= 1


@given(histories(), st.randoms(use_true_random=False))
@pure
def test_commit_order_moves_no_pair(change_sets, rng: random.Random):
    shuffled = rng.sample(change_sets, len(change_sets))

    assert (coupling.change_coupling(_log(shuffled), min_support=1, min_confidence=0, top=None)
            == coupling.change_coupling(_log(change_sets), min_support=1, min_confidence=0,
                                        top=None))


def test_git_spelling_quotes_as_git_does():
    """git-config core.quotePath: quote, backslash and control characters are
    always escaped; with quotePath off a non-ASCII byte is not."""
    assert git_spelling("src/a\tb.py") == '"src/a\\tb.py"'
    assert git_spelling('src/"q".py') == '"src/\\"q\\".py"'
    assert git_spelling("src/ü\x01.py") == '"src/ü\\001.py"'
    assert git_spelling("src/ü.py") == "src/ü.py"


@pytest.mark.process
def test_tracked_order_changes_nothing(make_repo, monkeypatch):
    """The tracked set filters and keys the stored ranking; its order does neither."""
    built = make_repo(specs.COUPLED)
    monkeypatch.setenv("GIT_TEST_DATE_NOW", str(specs.COUPLED_NOW))
    tracked = sorted(path for path in repos.git(built.root, "ls-files", "-z").split("\0") if path)
    first = coupling_cache.load_coupling(built.root, 12, tracked)
    key = (built.root / ".crapkit" / coupling_cache.CACHE_NAME).read_bytes()

    again = coupling_cache.load_coupling(built.root, 12, tracked[::-1])

    assert again == first
    assert (built.root / ".crapkit" / coupling_cache.CACHE_NAME).read_bytes() == key
