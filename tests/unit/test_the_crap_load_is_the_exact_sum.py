"""A run's CRAP load is the exact sum of its stored scores, on every surface.

crap_load is the sum of the CRAP scores, printed at 2 dp. Python 3.11's sum()
adds left to right (3.12 made it compensated) and SQLite's SUM() adds in scan
order before SQLite 3.43. The eleven rows below add up to 507.62500000000006,
a hair above the 2 dp tie 507.625, and printed 507.62 in one row order and
507.63 in another. trend also added each scope's rounded load and then the
scopes, so a run whose scope loads both rounded down printed 257.12 where
coverage printed 257.13. math.fsum returns the correctly rounded sum in any
order, and every surface reads it now.
"""
from types import SimpleNamespace
import math

import pytest
from crapkit.cli.scoring import _coverage_summary
from crapkit.digest import totals, totals_from_counts
from crapkit.packet import file_totals
from crapkit.score import ScoredRow, crap
from crapkit.store import SnapshotStore

# (scope, ccn, twelfths covered), in the order a 20,000-example property run drew them
TIE_ROWS = [("a", 3, 7), ("a", 4, 7), ("a", 8, 5), ("a", 22, 3), ("a", 25, 8), ("a", 10, 4),
            ("a", 13, 4), ("a", 25, 8), ("a", 3, 8), ("a", 5, 5), ("a", 10, 4)]
# scope a sums to 114.00000000000003 and scope b to the 2 dp tie 143.125; the two
# rounded loads add up to the tie 257.125, while all three scores sum to just above it
TWO_SCOPES = [("b", 27, 8), ("b", 23, 6), ("a", 18, 4)]


def rows_of(spec) -> list[ScoredRow]:
    return [ScoredRow(scope, f"src/{scope}.py", f"f{n}( )", 1 + n, 1 + n, ccn, ccn, ccn, 3, 1, 1,
                      twelfths / 12, "measured", crap(ccn, twelfths / 12), "ok")
            for n, (scope, ccn, twelfths) in enumerate(spec)]


def both_orders(spec) -> list[list[ScoredRow]]:
    rows = rows_of(spec)
    return [rows, sorted(rows, key=lambda row: row.crap)]


def printed(load: float) -> str:
    return f"{totals_from_counts(1, 0, load).crap_load:.2f}"


def test_the_eleven_scores_sum_to_just_above_a_2dp_tie():
    craps = [row.crap for row in rows_of(TIE_ROWS)]

    assert math.fsum(craps) == 507.62500000000006
    assert math.fsum(sorted(craps)) == math.fsum(craps)


def test_the_two_scope_loads_add_to_a_tie_the_three_scores_do_not():
    rows = rows_of(TWO_SCOPES)
    scopes = [math.fsum(r.crap for r in rows if r.scope == s) for s in "ab"]

    assert scopes == [114.00000000000003, 143.125]
    assert math.fsum(scopes) == 257.125
    assert math.fsum(r.crap for r in rows) == 257.12500000000006


@pytest.mark.parametrize("order", both_orders(TIE_ROWS), ids=["drawn", "by-crap"])
def test_the_run_totals_do_not_follow_row_order(order):
    assert f"{totals(order, target=6).crap_load:.2f}" == "507.63"


@pytest.mark.parametrize("order", both_orders(TIE_ROWS), ids=["drawn", "by-crap"])
def test_a_brief_s_file_totals_do_not_follow_row_order(order):
    assert f"{file_totals(order, {}, 6)['crap_load']:.2f}" == "507.63"


@pytest.mark.parametrize("order", both_orders(TIE_ROWS), ids=["drawn", "by-crap"])
def test_the_coverage_summary_does_not_follow_row_order(order):
    run = SimpleNamespace(scored=order, commit="c1", cache_hits=0, provenance={},
                          lane_errors={}, corpus=SimpleNamespace(files=1, skipped_max_bytes=0))
    cfg = SimpleNamespace(target=6, scope_targets={}, ceilings={"default": 6},
                          ceiling_of=lambda scope: 6)
    shape = SimpleNamespace(kind="coverage", unmeasured=[])

    summary = _coverage_summary(1, run, cfg, shape, "crap.sqlite")

    assert f"{summary['crap_load']:.2f}" == "507.63"
    assert f"{summary['by_scope']['a']['crap_load']:.2f}" == "507.63"


def stored(tmp_path, *orders) -> tuple[SnapshotStore, list[int]]:
    store = SnapshotStore(tmp_path / "crap.sqlite")
    return store, [store.write_run(commit=f"c{n}", tool_versions={}, rows=rows,
                                   lanes={"unit": {}}) for n, rows in enumerate(orders)]


def test_trend_reads_one_load_whatever_order_the_rows_were_written_in(tmp_path):
    store, run_ids = stored(tmp_path, *both_orders(TIE_ROWS))

    for _ in range(2):  # the fill, then the cached rollup
        whole = store.run_totals(target=6)
        by_scope = store.run_scope_totals(target=6)
        assert [printed(whole[rid][2]) for rid in run_ids] == ["507.63", "507.63"]
        assert [printed(by_scope[rid]["a"][2]) for rid in run_ids] == ["507.63", "507.63"]


def test_trend_s_whole_run_load_is_the_exact_sum_not_the_sum_of_the_scopes(tmp_path):
    rows = rows_of(TWO_SCOPES)
    store, (run_id,) = stored(tmp_path, rows)
    want = f"{totals(rows, target=6).crap_load:.2f}"

    for _ in range(2):  # the fill, then the cached rollup
        (_run, whole, by_scope) = store.history_totals(target=6)[0]
        assert (want, printed(whole[2]), printed(store.run_totals(target=6)[run_id][2])) == \
            ("257.13", "257.13", "257.13")
        assert {scope: printed(t[2]) for scope, t in by_scope.items()} == \
            {"a": "114.00", "b": "143.12"}


def test_a_rollup_a_build_before_the_exact_sum_stored_is_summed_again(tmp_path):
    """Rule 2 left the whole-run numbers out of the marker row. A store filled
    under that key must not hand its empty marker back as the run's totals."""
    store, (run_id,) = stored(tmp_path, rows_of(TWO_SCOPES))
    with store._conn:
        store._conn.executemany("INSERT INTO run_rollup VALUES (?, '[2,6,[]]', ?, ?, ?, ?)",
                                [(run_id, "", 0, 0, 0.0), (run_id, "a", 1, 1, 114.00000000000003),
                                 (run_id, "b", 2, 2, 143.125)])

    assert store.run_totals(target=6)[run_id] == (3, 3, 257.12500000000006)
