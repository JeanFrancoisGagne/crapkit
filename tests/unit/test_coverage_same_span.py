"""Line-only artifact data cannot distinguish separate same-span functions."""
import pytest

from crapkit.analyze import analyze_source
from crapkit.coverage_istanbul import FnCoverage
from crapkit.errors import ToolError
from crapkit.score import score_rows
from crapkit.snapshot import build_inventory_rows


def functions():
    source = "function live() { return 1; } function dead() { return 2; }"
    return build_inventory_rows({"src": analyze_source("app.ts", source)})


@pytest.mark.parametrize("hits", [(True, False), (True, True), (False, False), (True,)])
def test_matching_artifact_cannot_prove_same_span_function_coverage(hits):
    artifact = {"app.ts": [FnCoverage(str(n), 1, 1, hit, 0, 0) for n, hit in enumerate(hits)]}
    with pytest.raises(ToolError) as error:
        score_rows(functions(), artifact, lane_scopes={"src"})
    assert "app.ts:1" in str(error.value)
    assert "separate lines" in str(error.value)


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
