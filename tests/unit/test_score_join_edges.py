"""score's join and remedy at the edges its rules name, every value hand-computed.

README: CRAP = ccn^2 * (1 - cov)^3 + ccn, a function is over its ceiling when
CRAP > ceiling, and each scope may carry its own ceiling (the config's
default otherwise). score.py: two functions on one source span share one
measurement, so neither gets it; a scope with no lane is uncovered, and its
remedy is add-tests; and an exported row reads back as the row it was.
"""
import math

import pytest

from crapkit import score
from crapkit.score import (FnCoverage, ScoredRow, overlay_stale_coverage, parse_scored_row,
                           score_rows)
from crapkit.snapshot import InventoryRow


def inv(scope: str, path: str, name: str, start: int, end: int, ccn: int,
        occurrence: int = 0) -> InventoryRow:
    return InventoryRow(scope, path, name, start, end, ccn, ccn, ccn, end - start + 1, 1, 1,
                        0, occurrence)


def test_a_score_at_the_ceiling_s_rounding_allowance_is_not_over():
    """AT_CEILING absorbs a double's error above the ceiling; the next double is over."""
    edge = 30 * score.AT_CEILING

    assert (score.over_ceiling(edge, 30), score.over_ceiling(math.nextafter(edge, math.inf), 30)) \
        == (False, True)


def test_a_span_two_functions_share_is_measured_for_neither():
    """A no-lane row first does not stop the search: f and g on 5-9 share it."""
    rows = [inv("docs", "a.ts", "x", 1, 1, 1), inv("web", "a.ts", "f", 5, 9, 2, 1),
            inv("web", "a.ts", "g", 5, 9, 2, 2)]
    coverage = {"a.ts": [FnCoverage("f", 5, 9, True, 2, 2)]}

    scored = score_rows(rows, coverage, lane_scopes={"web"}, target=6)

    assert [(r.long_name, r.cov, r.flag) for r in scored] == [
        ("x", 0.0, "no-lane"), ("f", 0.0, "untested"), ("g", 0.0, "untested")]


def test_a_one_line_python_def_with_no_lane_is_owed_tests():
    """No lane measures it, so its def line shares no measurement: ccn 3 at cov 0
    scores 3^2 + 3 = 12, over 6."""
    [row] = score_rows([inv("docs", "m.py", "f", 3, 3, 3)], {}, lane_scopes={"web"}, target=6)

    assert (row.flag, row.crap, row.remedy) == ("no-lane", 12.0, "add-tests")


def test_a_scope_the_ceiling_map_does_not_name_takes_the_default():
    """ccn 7 over the default 6 is decompose, whatever other scopes allow."""
    [row] = score_rows([inv("web", "a.ts", "f", 1, 10, 7)], {}, lane_scopes={"web"}, target=6,
                       scope_targets={"lib": 10})

    assert row.remedy == "decompose"


def test_a_stale_overlay_judges_each_row_by_its_scope_s_ceiling():
    """ccn 5 at the baseline's cov 0 scores 5^2 + 5 = 30: over the default 6,
    at or under the scope's 50."""
    baseline = [ScoredRow("web", "a.ts", "f", 1, 10, 5, 5, 5, 10, 1, 1, 0.0, "measured", 30.0,
                          "add-tests")]

    [row] = overlay_stale_coverage([inv("web", "a.ts", "f", 1, 10, 5)], baseline,
                                   lane_scopes={"web"}, target=6, scope_targets={"web": 50})

    assert (row.crap, row.remedy) == (30.0, "ok")


def test_an_exported_row_reads_back_as_itself():
    row = ScoredRow("web", "a.ts", "f", 1, 10, 5, 5, 5, 10, 1, 1, 0.25, "measured", 15.546875,
                    "add-tests", 3, 2)
    line = list(score.scored_tsv_lines([row]))[1].rstrip("\n")

    assert parse_scored_row(line) == row


@pytest.mark.parametrize("line, message", [
    ("a\tb\tc", "scored row has 3 fields, expected 16 or 17: ['a', 'b', 'c']"),
    ("web\ta.ts\tf\t1\t10\t5\t5\t5\t10\t1\t1\t0.25\tmeasured\t15.5\tadd-tests\t3\t-1",
     "scored occurrence must be nonnegative"),
])
def test_an_unreadable_exported_row_is_refused_by_what_is_wrong(line, message):
    with pytest.raises(ValueError) as refused:
        parse_scored_row(line)

    assert str(refused.value) == message
