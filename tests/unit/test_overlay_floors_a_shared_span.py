"""rescore's preview floors a shared span to untested, as the coverage run does.

Two functions that sat on their own lines, both measured, get edited onto one
line span. The coverage run cannot tell whose branches are whose there, so it
scores both as uncovered and advises split-lines. The preview joined each by
name to its old measured number instead and called both ok, so verify then
failed functions the gate had passed.
"""
from crapkit.coverage_istanbul import FnCoverage
from crapkit.score import ScoredRow, overlay_stale_coverage, score_rows
from crapkit.snapshot import InventoryRow

APP = "src/app.js"

# The baseline run: f and g on separate lines, each fully covered.
BASELINE = [ScoredRow("src", APP, "f", 1, 3, 3, 3, 3, 3, 0, 0, 1.0, "measured", 3.0, "ok", 0, 1),
            ScoredRow("src", APP, "g", 5, 7, 3, 3, 3, 3, 0, 0, 1.0, "measured", 3.0, "ok", 0, 1)]

# The working tree: the edit moved both onto line 1.
MOVED = [InventoryRow("src", APP, "f", 1, 1, 3, 3, 3, 1, 0, 0, 0, 1),
         InventoryRow("src", APP, "g", 1, 1, 3, 3, 3, 1, 0, 0, 0, 2)]


def view(rows) -> list[tuple]:
    return [(r.long_name, r.flag, r.cov, r.crap, r.remedy) for r in rows]


def test_functions_moved_onto_one_span_preview_as_uncovered():
    preview = overlay_stale_coverage(MOVED, BASELINE, lane_scopes={"src"}, target=6)

    # ccn 3 at cov 0: 3 * 3 * 1 + 3 = 12, over the ceiling of 6 on a shared span.
    assert view(preview) == [("f", "untested", 0.0, 12.0, "split-lines"),
                             ("g", "untested", 0.0, 12.0, "split-lines")]


def test_the_preview_says_what_the_next_coverage_run_will_say():
    artifact = {APP: [FnCoverage("f", 1, 1, True, 2, 2, 3, 3),
                      FnCoverage("g", 1, 1, True, 2, 2, 3, 3)]}

    preview = overlay_stale_coverage(MOVED, BASELINE, lane_scopes={"src"}, target=6)
    measured = score_rows(MOVED, artifact, lane_scopes={"src"}, target=6)

    assert view(preview) == view(measured)


def test_a_function_alone_on_its_span_keeps_its_baseline_number():
    alone = [MOVED[0], MOVED[1]._replace(start=5, end=7, occurrence=1)]

    preview = overlay_stale_coverage(alone, BASELINE, lane_scopes={"src"}, target=6)

    assert view(preview) == [("f", "measured", 1.0, 3.0, "ok"), ("g", "measured", 1.0, 3.0, "ok")]


def test_a_scope_without_a_lane_on_a_shared_span_stays_no_lane():
    preview = overlay_stale_coverage(MOVED, BASELINE, lane_scopes=set(), target=6)

    assert [(r.flag, r.remedy) for r in preview] == [("no-lane", "add-tests"),
                                                     ("no-lane", "add-tests")]


def test_same_line_callbacks_on_one_span_preview_as_uncovered():
    """Occurrence tells the two callbacks apart, but not their branches."""
    def callback(occurrence):
        return InventoryRow("src", "a.ts", "(anonymous)", 1, 1, 2, 2, 2, 1, 0, 0,
                            occurrence=occurrence)

    saved = [ScoredRow("src", "a.ts", "(anonymous)", 1, 1, 2, 2, 2, 1, 0, 0, cov, "measured",
                       2.0, "ok", occurrence=occurrence) for occurrence, cov in ((1, 1.0), (2, 0.5))]

    preview = overlay_stale_coverage([callback(1), callback(2)], saved, lane_scopes={"src"})

    assert [(r.occurrence, r.flag, r.cov) for r in preview] == [(1, "untested", 0.0),
                                                                (2, "untested", 0.0)]
