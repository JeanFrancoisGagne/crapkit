"""Merge seam: two lizard passes (standard, modified) in, FunctionRecords with min-CCN out.

RawFn tuples mirror what the analyze shell extracts from lizard's API:
(path, long_name, start, end, ccn, nloc, params, nesting).
"""
import pytest

from crapkit.merge import RawFn, merge_passes


def raw(path="a.ts", name="f( x )", start=1, end=9, ccn=7, nloc=8, params=1, nesting=2):
    return RawFn(path, name, start, end, ccn, nloc, params, nesting)


def test_min_of_standard_and_modified_is_the_scored_ccn():
    std = [raw(ccn=7)]
    mod = [raw(ccn=5)]
    (rec,) = merge_passes(std, mod)
    assert rec.ccn_std == 7
    assert rec.ccn_mod == 5
    assert rec.ccn == 5


def test_functions_join_by_span_not_by_name_so_anonymous_twins_survive():
    std = [raw(name="(anonymous)", start=1, end=3, ccn=4), raw(name="(anonymous)", start=5, end=9, ccn=9)]
    mod = [raw(name="(anonymous)", start=1, end=3, ccn=4), raw(name="(anonymous)", start=5, end=9, ccn=6)]
    recs = merge_passes(std, mod)
    assert len(recs) == 2
    by_start = {r.start: r for r in recs}
    assert by_start[1].ccn == 4
    assert by_start[5].ccn == 6


def test_pass_mismatch_is_a_loud_error_not_a_silent_drop():
    std = [raw(start=1, end=3), raw(start=5, end=9)]
    mod = [raw(start=1, end=3)]
    with pytest.raises(ValueError, match="mismatch"):
        merge_passes(std, mod)


def test_metrics_carried_through_from_the_standard_pass():
    (rec,) = merge_passes([raw(nloc=42, params=3, nesting=4)], [raw(ccn=2)])
    assert rec.nloc == 42
    assert rec.params == 3
    assert rec.nesting == 4
    assert rec.path == "a.ts"
    assert rec.end == 9


def test_same_span_same_name_twins_pair_in_order_not_last_wins():
    std = [raw(name="handlers ( )", start=1, end=1, ccn=2), raw(name="handlers ( )", start=1, end=1, ccn=4)]
    mod = [raw(name="handlers ( )", start=1, end=1, ccn=2), raw(name="handlers ( )", start=1, end=1, ccn=3)]
    recs = merge_passes(std, mod)
    assert [(r.ccn_std, r.ccn_mod, r.ccn) for r in recs] == [(2, 2, 2), (4, 3, 3)]


def test_twin_count_mismatch_between_passes_is_loud():
    std = [raw(start=1, end=1), raw(start=1, end=1)]
    mod = [raw(start=1, end=1)]
    with pytest.raises(ValueError, match="mismatch"):
        merge_passes(std, mod)
