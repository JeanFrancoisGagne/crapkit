"""Line-only artifact data cannot distinguish separate same-span functions."""
import pytest

from crapkit.analyze import analyze_source
from crapkit.coverage_istanbul import FnCoverage
from crapkit.score import SharedSpanFold, overlay_stale_coverage, score_rows
from crapkit.snapshot import build_inventory_rows


def functions():
    source = "function live() { return 1; } function dead() { return 2; }"
    return build_inventory_rows({"src": analyze_source("app.ts", source)})


@pytest.mark.parametrize("hits", [(True, False), (True, True), (False, False), (True,)])
def test_a_matching_artifact_scores_both_same_span_functions_as_uncovered(hits):
    artifact = {"app.ts": [FnCoverage(str(n), 1, 1, hit, 0, 0) for n, hit in enumerate(hits)]}
    scored = score_rows(functions(), artifact, lane_scopes={"src"})
    assert [(r.flag, r.cov) for r in scored] == [("untested", 0.0), ("untested", 0.0)]


def test_the_fold_keeps_every_function_on_the_shared_span():
    fold = SharedSpanFold()
    score_rows(functions(), {"app.ts": [FnCoverage("live", 1, 1, True, 0, 0)]},
               lane_scopes={"src"}, shared_spans=fold)
    (members,) = fold.sites
    assert [(m.path, m.start, m.long_name) for m in members] == [
        ("app.ts", 1, "live ( )"), ("app.ts", 1, "dead ( )")]


def test_a_third_function_on_the_span_joins_its_members():
    first, second = functions()
    third = second._replace(long_name="third ( )", occurrence=3)
    fold = SharedSpanFold()
    score_rows([first, second, third], {"app.ts": [FnCoverage("live", 1, 1, True, 0, 0)]},
               lane_scopes={"src"}, shared_spans=fold)
    (members,) = fold.sites
    assert [m.long_name for m in members] == ["live ( )", "dead ( )", "third ( )"]


def test_a_second_scope_copy_of_a_colliding_function_is_not_a_second_member():
    """The same function measured in two scopes is one function on the span."""
    first, second = functions()
    fold = SharedSpanFold()
    score_rows([first, second, first._replace(scope="other")],
               {"app.ts": [FnCoverage("live", 1, 1, True, 0, 0)]},
               lane_scopes={"src", "other"}, shared_spans=fold)
    (members,) = fold.sites
    assert [m.long_name for m in members] == ["live ( )", "dead ( )"]


def test_a_span_no_measurement_speaks_about_is_not_collected():
    fold = SharedSpanFold()
    score_rows(functions(), {"app.ts": []}, lane_scopes={"src"}, shared_spans=fold)
    assert fold.sites == []


def test_repeated_scope_copies_of_one_function_are_not_a_collision():
    row = functions()[0]
    rows = [row, row._replace(scope="other")]
    scored = score_rows(rows, {"app.ts": [FnCoverage("live", 1, 1, True, 0, 0)]},
                        lane_scopes={"src", "other"})
    assert [(r.scope, r.cov) for r in scored] == [("src", 1.0), ("other", 1.0)]


@pytest.mark.parametrize("lane_scopes,cc_only,expected", [
    (set(), frozenset(), "no-lane"),
    ({"src"}, frozenset({"src"}), "cc-only"),
])
def test_scopes_without_measured_coverage_keep_their_verdict(lane_scopes, cc_only, expected):
    scored = score_rows(functions(), {"app.ts": [FnCoverage("live", 1, 1, True, 0, 0)]},
                        lane_scopes=lane_scopes, cc_only_scopes=cc_only)
    assert [r.flag for r in scored] == [expected, expected]


@pytest.mark.parametrize("artifact", [{}, {"app.ts": []},
                                       {"app.ts": [FnCoverage("other", 8, 9, True, 0, 0)]}])
def test_same_span_functions_without_matching_measurement_stay_untested(artifact):
    scored = score_rows(functions(), artifact, lane_scopes={"src"})
    assert [(r.flag, r.cov) for r in scored] == [("untested", 0.0), ("untested", 0.0)]


def test_different_spans_starting_on_one_line_keep_existing_attribution():
    first, second = functions()
    rows = [first._replace(end=2), second._replace(end=4)]
    scored = score_rows(rows, {"app.ts": [FnCoverage("live", 1, 2, True, 0, 0),
                                         FnCoverage("dead", 1, 4, False, 0, 0)]},
                        lane_scopes={"src"})
    assert [r.cov for r in scored] == [1.0, 0.0]


def shared_pair(ccn: int):
    """The pair on one line, the first complex enough to matter at the given ccn."""
    first, second = functions()
    return [first._replace(ccn=ccn), second]


SHARED_ARTIFACT = {"app.ts": [FnCoverage("live", 1, 1, True, 0, 0)]}


def test_a_shared_span_function_over_its_ceiling_is_told_to_split_the_line():
    """No test can lower its score: coverage cannot be told apart on that line,
    so add-tests would be advice nobody can follow."""
    scored = score_rows(shared_pair(3), SHARED_ARTIFACT, lane_scopes={"src"}, target=6)
    assert [(r.ccn, r.crap, r.remedy) for r in scored] == [(3, 12.0, "split-lines"), (1, 2.0, "ok")]


def test_a_shared_span_function_too_complex_for_any_coverage_is_still_decomposed():
    scored = score_rows(shared_pair(7), SHARED_ARTIFACT, lane_scopes={"src"}, target=6)
    assert scored[0].remedy == "decompose"


def test_an_unmeasured_function_alone_on_its_line_is_still_told_to_add_tests():
    alone = functions()[:1]
    scored = score_rows([alone[0]._replace(ccn=3)], {"app.ts": []}, lane_scopes={"src"}, target=6)
    assert [(r.flag, r.remedy) for r in scored] == [("untested", "add-tests")]


def test_a_shared_span_no_lane_measures_keeps_its_own_advice():
    """Without a lane the missing number is a tooling gap, not a shared line."""
    scored = score_rows(shared_pair(3), SHARED_ARTIFACT, lane_scopes=set(), target=6)
    assert [(r.flag, r.remedy) for r in scored] == [("no-lane", "add-tests"), ("no-lane", "ok")]


def test_a_shared_span_nothing_measures_is_told_to_split_the_line_too():
    """Tests would only make the line measured, and a measured shared line
    scores as uncovered: splitting comes first either way."""
    scored = score_rows(shared_pair(3), {"app.ts": []}, lane_scopes={"src"}, target=6)
    assert [(r.flag, r.remedy) for r in scored] == [("untested", "split-lines"), ("untested", "ok")]


def test_the_rescore_preview_gives_a_shared_span_the_same_advice():
    """The gate and check_gate read this path; they may not fall back to add-tests."""
    scored = overlay_stale_coverage(shared_pair(3), [], lane_scopes={"src"}, target=6)
    assert [(r.flag, r.remedy) for r in scored] == [("untested", "split-lines"), ("untested", "ok")]


def test_the_rescore_preview_leaves_a_scope_without_a_lane_alone():
    scored = overlay_stale_coverage(shared_pair(3), [], lane_scopes=set(), target=6)
    assert [(r.flag, r.remedy) for r in scored] == [("no-lane", "add-tests"), ("no-lane", "ok")]
