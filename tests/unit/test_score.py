"""Scoring seam: inventory rows + lane coverage in, scored rows with flags out. Pure."""
import pytest

from crapkit.coverage_istanbul import FnCoverage
from crapkit.score import crap, score_rows
from crapkit.snapshot import InventoryRow


def row(path="src/a.ts", name="f( )", start=1, end=13, ccn=7, scope="src"):
    return InventoryRow(scope, path, name, start, end, ccn + 2, ccn, ccn, 8, 1, 2)


def test_crap_formula_exact_values():
    assert crap(5, 1.0) == 5
    assert crap(5, 0.0) == 30
    assert crap(1, 0.0) == 2
    assert round(crap(6, 1.0), 6) == 6
    assert crap(7, 1.0) == 7, "cc 7 at full coverage still exceeds a target of 6"


def test_join_by_span_overlap_flags_measured():
    cov = {"src/a.ts": [FnCoverage("f", 1, 13, True, 4, 3)]}
    (scored,) = score_rows([row()], cov, lane_scopes={"src"})
    assert scored.flag == "measured"
    assert scored.cov == 0.75
    assert scored.crap == crap(7, 0.75)


def test_function_absent_from_artifact_scores_zero_untested():
    (scored,) = score_rows([row()], {"src/a.ts": []}, lane_scopes={"src"})
    assert scored.flag == "untested"
    assert scored.cov == 0.0
    assert scored.crap == crap(7, 0.0)


def test_scope_with_no_lane_flags_no_lane_never_conflated():
    (scored,) = score_rows([row(scope="ui")], {}, lane_scopes={"src"})
    assert scored.flag == "no-lane"
    assert scored.cov == 0.0


def test_remedy_column_add_tests_vs_decompose():
    easy, hard = score_rows([row(ccn=5), row(ccn=9, name="g( )", start=20, end=30)], {"src/a.ts": []}, lane_scopes={"src"})
    assert easy.remedy == "add-tests"
    assert hard.remedy == "decompose"


def test_covered_file_with_multiple_functions_joins_each_by_overlap():
    cov = {"src/a.ts": [FnCoverage("f", 1, 13, True, 2, 2), FnCoverage("g", 20, 30, False, 2, 0)]}
    rows = [row(), row(name="g( )", start=20, end=30, ccn=3)]
    f, g = score_rows(rows, cov, lane_scopes={"src"})
    assert f.cov == 1.0 and f.flag == "measured"
    assert g.cov == 0.0 and g.flag == "measured"


def test_span_tie_prefers_exact_start_then_tightest_span():
    outer = FnCoverage("nested_outer", 25, 30, True, 2, 2)
    inner = FnCoverage("inner", 26, 29, True, 2, 1)
    cov = {"src/a.ts": [outer, inner]}
    (scored,) = score_rows([row(start=26, end=29, ccn=7)], cov, lane_scopes={"src"})
    assert scored.cov == 0.5, "the nested function must join its own entry, not the enclosing one"


def test_overlay_prefers_name_match_when_spans_shift():
    from crapkit.score import ScoredRow, overlay_stale_coverage
    baseline = [
        ScoredRow("src", "src/a.ts", "alpha( )", 1, 10, 5, 5, 5, 8, 1, 1, 0.9, "measured", 5.005, "ok"),
        ScoredRow("src", "src/a.ts", "beta( )", 12, 20, 5, 5, 5, 8, 1, 1, 0.05, "measured", 26.4, "add-tests"),
    ]
    fresh = [row(name="alpha( )", start=16, end=25, ccn=5), row(name="beta( )", start=27, end=35, ccn=5)]
    scored = {r.long_name: r for r in overlay_stale_coverage(fresh, baseline, lane_scopes={"src"})}
    assert scored["alpha( )"].cov == 0.9, "shifted alpha keeps ITS coverage, not beta's"
    assert scored["beta( )"].cov == 0.05


def test_identical_span_twins_from_two_lanes_keep_the_better_measurement():
    a = FnCoverage("f( )", 1, 13, True, 4, 0)
    b = FnCoverage("f( )", 1, 13, True, 4, 4)
    for order in ([a, b], [b, a]):
        (scored,) = score_rows([row(ccn=7, start=1, end=13)], {"src/a.ts": list(order)},
                               lane_scopes={"src"})
        assert scored.cov == 1.0, "lane declaration order must never change a score"


def test_remedy_respects_the_configured_target():
    (scored,) = score_rows([row(ccn=8)], {"src/a.ts": [FnCoverage("f( )", 1, 13, True, 4, 4)]},
                           lane_scopes={"src"}, target=10)
    assert scored.remedy == "ok", "ccn 8 fully covered is fine under target 10"
    (scored6,) = score_rows([row(ccn=8)], {"src/a.ts": [FnCoverage("f( )", 1, 13, True, 4, 4)]},
                            lane_scopes={"src"}, target=6)
    assert scored6.remedy == "decompose"


def test_overlay_renamed_function_never_inherits_a_neighbours_coverage():
    from crapkit.score import ScoredRow, overlay_stale_coverage
    baseline = [ScoredRow("src", "src/a.ts", "outer( )", 1, 40, 5, 5, 5, 8, 1, 1, 0.9,
                          "measured", 5.005, "ok")]
    # outer( ) still exists (its named match creates a stub); the RENAMED
    # function nests inside outer's span and must not span-join that stub.
    fresh = [row(name="outer( )", start=1, end=40, ccn=5),
             row(name="renamed( )", start=10, end=20, ccn=5)]
    scored = {r.long_name: r for r in overlay_stale_coverage(fresh, baseline, lane_scopes={"src"})}
    assert scored["outer( )"].cov == 0.9
    assert scored["renamed( )"].flag == "untested" and scored["renamed( )"].cov == 0.0, \
        "a renamed function has no baseline identity; inheriting outer( )'s stale 0.9 misleads the preview"


def test_scope_targets_pick_the_ceiling_per_row():
    rows = [row(ccn=8, scope="src"), row(ccn=8, scope="legacy", path="old/a.py")]
    cov = {"src/a.ts": [FnCoverage("f( )", 1, 13, True, 4, 4)],
           "old/a.py": [FnCoverage("f( )", 1, 13, True, 4, 4)]}
    strict, lenient = score_rows(rows, cov, lane_scopes={"src", "legacy"}, target=6,
                                 scope_targets={"src": 6, "legacy": 10})
    assert strict.remedy == "decompose", "ccn 8 breaks the src ceiling of 6"
    assert lenient.remedy == "ok", "ccn 8 fully covered clears the legacy ceiling of 10"


def test_overlay_picks_the_nearest_same_name_twin_and_never_a_twin_in_another_file():
    from crapkit.score import ScoredRow, overlay_stale_coverage

    def measured(path, name, start, cov):
        return ScoredRow("src", path, name, start, start + 5, 5, 5, 5, 6, 1, 1, cov,
                         "measured", 5.0, "ok")

    # b.ts holds the nearest f( ) of all (one line away); a.ts's own is two away.
    baseline = [measured("src/a.ts", "f( )", 10, 0.1), measured("src/a.ts", "f( )", 90, 0.9),
                measured("src/b.ts", "f( )", 89, 0.4)]
    fresh = [row(path="src/a.ts", name="f( )", start=88, end=94, ccn=5)]
    (scored,) = overlay_stale_coverage(fresh, baseline, lane_scopes={"src"})
    assert scored.cov == 0.9, "nearest start among the SAME file's twins wins, never another file's"
    assert scored.flag == "measured"


def test_overlay_equidistant_twins_keep_the_first_one_the_baseline_recorded():
    from crapkit.score import ScoredRow, overlay_stale_coverage

    def measured(start, cov):
        return ScoredRow("src", "src/a.ts", "f( )", start, start + 5, 5, 5, 5, 6, 1, 1, cov,
                         "measured", 5.0, "ok")

    fresh = [row(path="src/a.ts", name="f( )", start=20, end=26, ccn=5)]
    baseline = [measured(10, 0.1), measured(30, 0.9)]  # both 10 lines away
    (first_wins,) = overlay_stale_coverage(fresh, baseline, lane_scopes={"src"})
    (order_flipped,) = overlay_stale_coverage(fresh, list(reversed(baseline)), lane_scopes={"src"})
    assert first_wins.cov == 0.1
    assert order_flipped.cov == 0.9, \
        "the tie-break is the baseline's own order; grouping by name must not reorder it"


def test_overlay_ignores_unmeasured_baseline_rows_with_the_same_name():
    from crapkit.score import ScoredRow, overlay_stale_coverage
    baseline = [ScoredRow("src", "src/a.ts", "f( )", 1, 6, 5, 5, 5, 6, 1, 1, 0.0,
                          "untested", 5.0, "add-tests")]
    (scored,) = overlay_stale_coverage([row(path="src/a.ts", name="f( )", start=1, end=6, ccn=5)],
                                       baseline, lane_scopes={"src"})
    assert scored.flag == "untested", "only measured baseline rows carry coverage forward"
