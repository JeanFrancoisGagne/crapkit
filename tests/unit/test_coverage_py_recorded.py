"""coverage.py's own reports over one probe file: a report that names each
start_line scores every function, and one that names none is refused by name.

coverage.py 7.10.6 and 7.16.1 measured the same run of
tests/fixtures/recorded/coveragepy_regions/probe.py. 7.16.1 writes each
region's start_line. 7.10.6 writes none, and no line inside a region is its
def's, so the reader refuses the report and names the coverage.py release
that writes one (the py extra's floor, coverage>=7.13.1). The probe holds the
shapes that can go wrong: one-line defs at module level, nested, opening a
body, after a statement, in a class, under a decorator and under `# pragma: no
cover`; defs whose body is one statement; defs whose body is one-line defs;
and defs with nothing but a docstring.
"""
from pathlib import Path

import pytest

from crapkit.analyze import analyze_source
from crapkit.coverage_py import parse_coveragepy_both_file
from crapkit.errors import ToolError
from crapkit.score import score_rows
from crapkit.snapshot import build_inventory_rows

RECORDED = Path(__file__).parents[1] / "fixtures" / "recorded" / "coveragepy_regions"


def _scores(version: str, mode: str) -> dict[str, tuple]:
    source = (RECORDED / "probe.py").read_text(encoding="utf-8")
    rows = build_inventory_rows({"s": analyze_source("probe.py", source, note=False)})
    report = RECORDED / f"coverage-{version}-{mode}.json"
    per_file = parse_coveragepy_both_file(report, path_prefix="")[0]
    return {row.long_name: ((row.cov, row.crap), row.flag)
            for row in score_rows(rows, per_file, lane_scopes={"s"})}


@pytest.mark.parametrize("mode", ["branch", "statement"])
def test_a_report_that_names_each_start_line_scores_every_function(mode):
    assert len(_scores("7.16.1", mode)) == 53


@pytest.mark.parametrize("mode", ["branch", "statement"])
def test_a_report_with_no_start_line_is_refused_by_name(mode):
    with pytest.raises(ToolError) as refused:
        _scores("7.10.6", mode)

    assert "no start_line; coverage.py writes it on every function from 7.13.1" in str(refused.value)
