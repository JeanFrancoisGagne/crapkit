"""coverage_py at its edges: absent keys and the warnings' words.

A region line list the report leaves out reads as empty. A summary with no
count, or no summary at all, is refused by name: coverage.py writes one on
every region, so its absence is a report something else rewrote. A region with
no start_line is refused too (test_coverage_py_recorded). Each warning names
the lane that read the report, and a sample names three paths and counts the
rest.
"""
import json
from types import SimpleNamespace

import pytest

from crapkit import coverage_py, covstream
from crapkit.named import first_few

NO_BRANCH = ("coverage.py report carries no branch data, so the coverage term is statement-based "
             "for this artifact - add --cov-branch to the lane command to measure branches\n")
REGION = {"summary": {"num_statements": 1, "covered_lines": 1}, "executed_lines": [2], "start_line": 1}


def report(tmp_path, files: dict, meta: dict | None = None):
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"meta": meta or {}, "files": files}), encoding="utf-8")
    return path


def test_a_summary_with_no_count_is_refused_and_a_bad_count_is_named():
    with pytest.raises(ValueError) as empty:
        coverage_py._admit_summary("f", {})
    with pytest.raises(ValueError) as caught:
        coverage_py._admit_summary("f", {"num_statements": 1, "covered_lines": 1, "excluded_lines": -1})

    assert str(empty.value).startswith("f: summary holds neither statement nor branch counts")
    assert str(caught.value).startswith("f: excluded_lines must be a nonnegative integer count, got -1")


def test_a_region_with_no_summary_is_refused_and_no_line_list_reads_as_empty():
    with pytest.raises(ValueError) as caught:
        coverage_py._file_functions({"functions": {"f": {"start_line": 3}}})

    assert str(caught.value).startswith("f: no summary object")
    assert coverage_py._region_lines("f", {"excluded_lines": [4]}) == [4]
    assert coverage_py._region_lines("f", {"executed_lines": [1]}) == [1]


def test_only_a_meta_object_says_whether_branches_were_measured():
    members = (("meta", []), ("totals", {"branch_coverage": True}), ("meta", {"branch_coverage": True}))

    assert [coverage_py._meta_has_branch(key, value) for key, value in members] == [False, False, True]


def test_a_sample_names_three_paths_in_order_and_counts_the_rest():
    assert [first_few(sorted(paths)) for paths in (list("cabed"), list("abc"), list("abcd"))] == [
        "a, b, c and 2 more", "a, b, c", "a, b, c and 1 more"]
    assert coverage_py._named("") == ""
    assert coverage_py.lane_prefix("libX/") == "libX/"


def test_the_warnings_name_the_lane_when_one_is_given_and_nothing_otherwise(capsys):
    per_file = {"a.py": [SimpleNamespace(statements_total=1)]}

    coverage_py.judge_branch(False, per_file)
    coverage_py.judge_regions(["b.py"], 2, "lane 'x'")
    coverage_py.judge_regions(["b.py"], 2)

    regions = ("coverage.py report has no function regions for 1 of 2 file(s) (b.py) - those files "
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

