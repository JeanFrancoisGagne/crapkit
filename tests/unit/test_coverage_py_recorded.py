"""coverage.py's own reports over one probe file: a report that names no
start_line scores each function as a report that names it does.

coverage.py 7.10.6 and 7.16.1 measured the same run of
tests/fixtures/recorded/coveragepy_regions/probe.py. 7.16.1 writes each
region's start_line, so its scores are the oracle. 7.10.6 writes none, so the
reader finds every def statement from the lines alone. The probe holds the
shapes that can go wrong: one-line defs at module level, nested, opening a
body, after a statement, in a class, under a decorator and under `# pragma: no
cover`; defs whose body is one statement; defs whose body is one-line defs;
and defs with nothing but a docstring. The branch reports list the arcs a
one-line def returns by, and the statement reports list none.
"""
from pathlib import Path

import pytest

from crapkit.analyze import analyze_source
from crapkit.coverage_py import parse_coveragepy_both_file
from crapkit.score import score_rows
from crapkit.snapshot import build_inventory_rows

RECORDED = Path(__file__).parents[1] / "fixtures" / "recorded" / "coveragepy_regions"


def _scores(version: str, mode: str) -> dict[str, tuple]:
    source = (RECORDED / "probe.py").read_text(encoding="utf-8")
    rows = build_inventory_rows({"s": analyze_source("probe.py", source, note=False)})
    report = RECORDED / f"coverage-{version}-{mode}.json"
    per_file = parse_coveragepy_both_file(report, path_prefix="")[0]
    return {row.long_name: (row.cov, row.crap, row.flag)
            for row in score_rows(rows, per_file, lane_scopes={"s"})}


@pytest.mark.parametrize("mode", ["branch", "statement"])
def test_a_report_with_no_start_line_scores_each_function_as_one_that_names_it(mode):
    """A def with nothing but a docstring holds no line, so 7.10.6 does not
    place it. At module level it reads untested at the number its region
    holds, cov 0. Nested, it takes a neighbour's number, which its ccn of 1
    keeps between crap 1 and 2. Every other function scores as under 7.16.1."""
    named, found = _scores("7.16.1", mode), _scores("7.10.6", mode)

    moved = {name for name in named if found[name][:2] != named[name][:2]}
    relabelled = {name for name in named if found[name][2] != named[name][2]}

    assert len(named) == 53
    assert moved == {"nested_doc_then_one.doc_only( )"}
    assert relabelled == {"only_doc( )"}
    assert found["only_doc( )"][2] == "untested" and named["only_doc( )"][2] == "measured"
