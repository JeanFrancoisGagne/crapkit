"""3-way ratchet merge (git merge driver semantics per key): marks only fall,
a deliberate drop beats an unchanged copy, and concurrent improvements meet at min."""
from crapkit.ratchet import RatchetEntry, merge_ratchets


def e(name: str, crap: float) -> RatchetEntry:
    return RatchetEntry("src/a.ts", name, crap)


def test_both_sides_lowered_takes_the_min():
    merged = merge_ratchets([e("f", 50.0)], [e("f", 30.0)], [e("f", 20.0)])
    assert merged == [e("f", 20.0)]


def test_a_drop_beats_an_unchanged_copy():
    merged = merge_ratchets([e("f", 50.0)], [], [e("f", 50.0)])
    assert merged == [], "one side fixed or pruned it; the other never touched it"


def test_a_drop_loses_to_a_concurrent_lowering():
    merged = merge_ratchets([e("f", 50.0)], [], [e("f", 20.0)])
    assert merged == [e("f", 20.0)], "keep the mark: it can only fall, and prune is re-runnable"


def test_new_debt_on_one_side_is_kept():
    merged = merge_ratchets([], [e("f", 40.0)], [])
    assert merged == [e("f", 40.0)]


def test_identical_sides_pass_through():
    merged = merge_ratchets([e("f", 50.0)], [e("f", 50.0)], [e("f", 50.0)])
    assert merged == [e("f", 50.0)]


def test_disjoint_new_debt_unions_sorted():
    merged = merge_ratchets([], [RatchetEntry("src/b.ts", "g", 10.0)], [e("f", 40.0)])
    assert merged == [e("f", 40.0), RatchetEntry("src/b.ts", "g", 10.0)]
