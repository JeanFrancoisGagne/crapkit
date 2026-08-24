"""Batch splitting: one worklist into N sessions that cannot collide.

Two agents editing the same file collide in the worktree whatever the ranking
says, so a batch owns whole files, and files that keep landing in the same
commit are one unit even though nothing imports them from each other.
"""
import pytest
from crapkit.worklist import WorklistEntry, split_batches


def entry(path: str, name: str = "f( )", risk: float = 10.0, ccn: int = 9) -> WorklistEntry:
    return WorklistEntry("core", path, name, 1, 20, ccn, ccn, 15, 3, 1, 1.0, risk)


def pair(a: str, b: str, confidence: float = 1.0) -> dict:
    return {"files": [a, b], "support": 6, "confidence": confidence}


FOUR = [entry("a.py", risk=10.0), entry("b.py", risk=8.0),
        entry("c.py", risk=6.0), entry("d.py", risk=4.0)]


def files_of(batches) -> list[list[str]]:
    return [b.files for b in batches]


def test_uncoupled_files_fill_the_lightest_batch_first():
    # risks 10, 8, 6, 4 into two bins: a->0, b->1, c->1 (8 < 10), d->0 (10 < 14)
    assert files_of(split_batches(FOUR, [], batches=2)) == [["a.py", "d.py"], ["b.py", "c.py"]]


def test_no_two_batches_ever_share_a_file():
    batches = split_batches(FOUR, [], batches=3)
    seen = [f for b in batches for f in b.files]
    assert sorted(seen) == ["a.py", "b.py", "c.py", "d.py"]
    assert len(seen) == len(set(seen))


def test_two_functions_in_one_file_are_never_split_apart():
    entries = [entry("a.py", "one( )", risk=10.0), entry("a.py", "two( )", risk=9.0),
               entry("b.py", risk=8.0)]
    batches = split_batches(entries, [], batches=4)
    assert files_of(batches) == [["a.py"], ["b.py"]]
    assert len(batches[0].entries) == 2


def test_co_changing_files_land_in_the_same_batch():
    # a and b would go to opposite bins on risk alone; the coupling makes them
    # one unit, so the split falls between the pair and the rest
    batches = split_batches(FOUR, [pair("a.py", "b.py")], batches=2)
    assert files_of(batches) == [["a.py", "b.py"], ["c.py", "d.py"]]


def test_coupling_below_the_containment_threshold_does_not_merge():
    weak = [pair("a.py", "b.py", confidence=0.49)]
    assert files_of(split_batches(FOUR, weak, batches=2)) == [["a.py", "d.py"], ["b.py", "c.py"]]


def test_a_coupling_chain_collapses_into_one_unit():
    chain = [pair("a.py", "b.py"), pair("b.py", "c.py")]
    batches = split_batches(FOUR, chain, batches=3)
    assert files_of(batches) == [["a.py", "b.py", "c.py"], ["d.py"]]


def test_one_batch_holds_everything_in_rank_order():
    # the units arrive as {a, c} then {b}; a batch must still read top down
    batches = split_batches(FOUR, [pair("a.py", "c.py")], batches=1)
    assert len(batches) == 1
    assert [e.path for e in batches[0].entries] == ["a.py", "b.py", "c.py", "d.py"]


def test_fewer_units_than_batches_returns_fewer_batches_not_empty_ones():
    batches = split_batches(FOUR[:2], [], batches=5)
    assert files_of(batches) == [["a.py"], ["b.py"]]


def test_an_empty_worklist_produces_no_batches():
    assert split_batches([], [pair("a.py", "b.py")], batches=3) == []


def test_the_same_worklist_and_history_always_split_the_same_way():
    pairs = [pair("c.py", "d.py"), pair("a.py", "b.py")]
    first = split_batches(FOUR, pairs, batches=2)
    reversed_pairs = split_batches(FOUR, list(reversed(pairs)), batches=2)
    assert files_of(first) == files_of(reversed_pairs)
    assert files_of(first) == [["a.py", "b.py"], ["c.py", "d.py"]]


def test_a_pair_naming_a_file_no_worklist_entry_mentions_is_harmless():
    batches = split_batches(FOUR, [pair("a.py", "zz.py")], batches=2)
    seen = [f for b in batches for f in b.files]
    assert sorted(seen) == ["a.py", "b.py", "c.py", "d.py"], "zz.py has nothing to batch"


def test_non_positive_batch_count_is_rejected_loudly():
    with pytest.raises(ValueError, match="batches"):
        split_batches(FOUR, [], batches=0)
