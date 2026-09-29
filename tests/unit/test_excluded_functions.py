"""A function its coverage tool was told not to measure scores `excluded`.

A `# pragma: no cover` def, an `/* istanbul ignore next */` or `/* v8 ignore
next */` function: the producer leaves it out on purpose, so no test can move
its number. It scores crap = ccn, the way a coverage_optional scope does, and
its remedy can only be ok or decompose. Scored at cov 0 it read
crap = ccn^2 + ccn with add-tests advice no test could satisfy.
"""
from crapkit.cli.scoring import _bucket_text, _flag_counts
from crapkit.coverage_istanbul import FnCoverage
from crapkit.score import overlay_stale_coverage, score_rows
from crapkit.snapshot import InventoryRow

LANE = {"s"}


def _row(path, name, start, end, ccn):
    return InventoryRow("s", path, f"{name}( x )", start, end, ccn, ccn, ccn, end - start + 1, 1, 1)


def _verdicts(scored):
    return [(row.long_name.split("(")[0], row.flag, row.crap, row.remedy) for row in scored]


def test_a_region_coverage_py_excluded_scores_its_ccn_with_no_tests_owed():
    rows = [_row("m.py", "kept", 1, 4, 5), _row("m.py", "excluded", 6, 9, 5),
            _row("m.py", "big", 11, 30, 8)]
    coverage = {"m.py": [FnCoverage("kept", 1, 4, True, 2, 1),
                         FnCoverage("excluded", 6, 9, False, 0, 0, excluded=True),
                         FnCoverage("big", 11, 30, False, 0, 0, excluded=True)]}

    scored = score_rows(rows, coverage, lane_scopes=LANE, target=6)

    assert _verdicts(scored) == [("kept", "measured", 8.125, "add-tests"),
                                 ("excluded", "excluded", 5.0, "ok"),
                                 ("big", "excluded", 8.0, "decompose")]


def test_a_function_an_ignore_hint_drops_from_the_artifact_reads_excluded():
    """istanbul lists every function it instruments, so a function in a file
    it measured that no entry covers was dropped by a hint. A file the
    artifact lists with no function at all proves nothing, and a file it does
    not list stays untested."""
    rows = [_row("a.js", "kept", 1, 5, 3), _row("a.js", "ignored", 7, 12, 3),
            _row("b.js", "bare", 1, 5, 3), _row("c.js", "unread", 1, 5, 3)]
    coverage = {"a.js": [FnCoverage("kept", 1, 5, True, 2, 2, full_listing=True)], "b.js": []}

    scored = score_rows(rows, coverage, lane_scopes=LANE, target=6)

    assert _verdicts(scored) == [("kept", "measured", 3.0, "ok"),
                                 ("ignored", "excluded", 3.0, "ok"),
                                 ("bare", "untested", 12.0, "add-tests"),
                                 ("unread", "untested", 12.0, "add-tests")]


def test_an_excluded_one_line_def_is_excluded_not_floored():
    """The def-line floor is for a def coverage.py measures but cannot show a
    call to. A one-line def under `# pragma: no cover` is not measured at all."""
    rows = [_row("m.py", "one", 3, 3, 2), _row("m.py", "measured_one", 5, 5, 2)]
    coverage = {"m.py": [FnCoverage("one", 3, 3, False, 0, 0, excluded=True),
                         FnCoverage("measured_one", 5, 5, True, 2, 2, 1, 1)]}

    scored = score_rows(rows, coverage, lane_scopes=LANE, target=6)

    assert _verdicts(scored) == [("one", "excluded", 2.0, "ok"),
                                 ("measured_one", "untested", 6.0, "ok")]


def test_rescore_keeps_an_excluded_function_excluded():
    """rescore overlays the baseline by name. It joined measured rows only, so
    an excluded function read untested in the preview and excluded in the
    next coverage run."""
    rows = [_row("m.py", "excluded", 6, 10, 7), _row("m.py", "one", 12, 12, 2)]
    coverage = {"m.py": [FnCoverage("excluded", 6, 9, False, 0, 0, excluded=True),
                         FnCoverage("one", 12, 12, False, 0, 0, excluded=True)]}
    baseline = score_rows([_row("m.py", "excluded", 6, 9, 5), _row("m.py", "one", 12, 12, 2)],
                          coverage, lane_scopes=LANE, target=6)

    scored = overlay_stale_coverage(rows, baseline, lane_scopes=LANE, target=6)

    assert _verdicts(scored) == [("excluded", "excluded", 7.0, "decompose"),
                                 ("one", "excluded", 2.0, "ok")]


def test_the_run_summary_counts_excluded_functions():
    """The five flags sum to the functions scored, and the one-line summary
    names the excluded ones: `1 measured / 1 excluded`."""
    rows = [_row("a.js", "kept", 1, 5, 3), _row("a.js", "ignored", 7, 12, 3)]
    scored = score_rows(rows, {"a.js": [FnCoverage("kept", 1, 5, True, 2, 2, full_listing=True)]},
                        lane_scopes=LANE, target=6)
    counts = _flag_counts(scored)
    summary = {"measured": counts["measured"], "untested": counts["untested"],
               "excluded": counts["excluded"], "no_lane": counts["no-lane"],
               "cc_only": counts["cc-only"]}

    assert (sum(counts.values()), _bucket_text(summary)) == (2, "1 measured / 1 excluded")
