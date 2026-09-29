"""coverage_py at its edges: absent keys, exit arcs, the def line ahead of a body and the warnings' words.

A region or summary key the report leaves out reads as empty or zero. An exit
arc is a two-item list whose second item is a negative int. With no start_line,
a region's def statement is the statement just ahead of its body when the region
around it holds that statement, and the region's own first line when nothing is
ahead. Each warning names the lane that read the report, and a sample names
three paths and counts the rest.
"""
import json
from types import SimpleNamespace

import pytest

from crapkit import coverage_py, covstream
from crapkit.coverage_istanbul import FnCoverage

ZERO = {"excluded_lines": 0, "num_branches": 0, "covered_branches": 0, "num_statements": 0,
        "covered_lines": 0}
NO_BRANCH = ("coverage.py report carries no branch data, so the coverage term is statement-based "
             "for this artifact — add --cov-branch to the lane command to measure branches\n")
REGION = {"summary": {"num_statements": 1, "covered_lines": 1}, "executed_lines": [2], "start_line": 1}


def report(tmp_path, files: dict, meta: dict | None = None):
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"meta": meta or {}, "files": files}), encoding="utf-8")
    return path


def test_a_summary_missing_every_count_admits_zeros_and_a_bad_count_is_named():
    assert coverage_py._admit_summary("f", {}) == ZERO

    with pytest.raises(ValueError) as caught:
        coverage_py._admit_summary("f", {"excluded_lines": -1})

    assert str(caught.value) == "f: excluded_lines must be a nonnegative integer count, got -1"


def test_a_region_with_no_summary_and_no_line_lists_reads_as_empty():
    assert coverage_py._file_functions({"functions": {"f": {"start_line": 3}}}) == [
        FnCoverage(name="f", start=3, end=3, invoked=False, branches_total=0, branches_covered=0,
                   statements_total=0, statements_covered=0, excluded=False)]
    assert coverage_py._region_lines({"excluded_lines": [4]}) == [4]
    assert coverage_py._region_lines({"executed_lines": [1]}) == [1]


def test_an_exit_arc_is_a_pair_ending_in_a_negative_int():
    arcs = ([5, -3], [5, 7], [5, 0], [5, -2.0], (5, -3))

    assert [coverage_py._is_exit(arc) for arc in arcs] == [True, False, False, False, False]
    assert coverage_py._returns_to({"missing_branches": [[3, -1]]}) == {1}


def test_only_a_meta_object_says_whether_branches_were_measured():
    members = (("meta", []), ("totals", {"branch_coverage": True}), ("meta", {"branch_coverage": True}))

    assert [coverage_py._meta_has_branch(key, value) for key, value in members] == [False, False, True]


def test_a_sample_names_three_paths_in_order_and_counts_the_rest():
    assert [coverage_py._sample(paths) for paths in (list("cabed"), list("abc"), list("abcd"))] == [
        "a, b, c and 2 more", "a, b, c", "a, b, c and 1 more"]
    assert coverage_py._named("") == ""
    assert coverage_py.lane_prefix("libX/") == "libX/"


def test_the_warnings_name_the_lane_when_one_is_given_and_nothing_otherwise(capsys):
    per_file = {"a.py": [SimpleNamespace(statements_total=1)]}

    coverage_py.judge_branch(False, per_file)
    coverage_py.judge_regions(["b.py"], 2, "lane 'x'")
    coverage_py.judge_regions(["b.py"], 2)

    regions = ("coverage.py report has no function regions for 1 of 2 file(s) (b.py) — those files "
               "are skipped and the rest of the report is scored\n")
    assert capsys.readouterr().err == (
        f"crapkit: {NO_BRANCH}crapkit: lane 'x': {regions}crapkit: {regions}")


def test_a_lane_reading_a_report_names_itself_in_both_warnings(tmp_path, capsys):
    path = report(tmp_path, {"a.py": {"functions": {"f": REGION}}, "b.py": {}})
    lane = SimpleNamespace(name="unit", path_prefix="")

    coverage_py.read(lane, tmp_path, path)
    coverage_py.parse_coveragepy_both_file(path, path_prefix="")

    err = capsys.readouterr().err.splitlines()
    assert [line.split("coverage.py report")[0] for line in err] == [
        "crapkit: lane 'unit': ", "crapkit: lane 'unit': ", "crapkit: ", "crapkit: "]


def test_a_file_with_no_missing_lines_key_misses_nothing(tmp_path):
    path = report(tmp_path, {"a.py": {}})

    assert coverage_py.parse_coveragepy_missing_file(path, path_prefix="") == {"a.py": set()}


def test_the_file_readers_read_in_the_chunk_they_are_given(tmp_path, monkeypatch):
    chunks = []
    monkeypatch.setattr(covstream, "read_walk",
                        lambda path, walk, message, chunk=None: chunks.append(chunk) or ({}, ""))

    coverage_py.parse_coveragepy_missing_file("r.json", path_prefix="", chunk=7)
    coverage_py.parse_coveragepy_contexts_file("r.json", path_prefix="", source_path="a.py", chunk=9)

    assert chunks == [7, 9]


def test_with_nothing_ahead_a_region_starts_on_its_own_first_line():
    statements = coverage_py._Statements({"f": {"executed_lines": [5]}, "f.g": {"executed_lines": [2, 3]}})

    assert statements.def_line("f.g", {}, [2, 3]) == 2


def test_a_def_statement_first_in_its_encloser_is_found_ahead_of_the_body():
    one_line = coverage_py._Statements({"f": {"executed_lines": [2, 5]}, "f.g": {"executed_lines": [3]}})
    two_line = coverage_py._Statements({"f": {"executed_lines": [2, 6]}, "f.g": {"executed_lines": [3, 4]}})
    module = coverage_py._Statements({"": {"executed_lines": [1]}, "g": {"executed_lines": [2, 3]}})

    assert one_line.def_line("f.g", {}, [3]) == 2
    assert two_line.def_line("f.g", {}, [3, 4]) == 2
    assert module.def_line("g", {}, [2, 3]) == 1
