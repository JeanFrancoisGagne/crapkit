"""The action's comment renderer on payloads with fields missing, and its CLI.

tools/action/comment.py reads crapkit's JSON and nothing else, so an older
crapkit's payload, a skipped step's empty file and a push with no changed-file
list all render; a missing count reads 0, a missing score `-` and a missing
grade `?`. The sentences are quoted whole, since a reviewer acts on them.
"""
import argparse
import importlib.util
import json
from functools import lru_cache
from pathlib import Path

import pytest

BUILDER = Path(__file__).resolve().parents[2] / "tools" / "action" / "comment.py"


@lru_cache(maxsize=None)
def builder():
    """Loaded by path, the way the action runs it."""
    spec = importlib.util.spec_from_file_location("crapkit_action_comment_render", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_summary_with_no_counts_reads_zero_and_no_grade_reads_a_question_mark():
    c = builder()

    assert (c.scored_line({"ceilings": {"default": 6}}), c.scored_line({"grade": "B"}),
            c.scored_line(None)) == (
        "0 functions in 0 files, 0 over ceiling 6, CRAP load 0, grade ?.",
        "0 functions in 0 files, 0 over the ceiling, CRAP load 0, grade B.",
        "`crapkit coverage` wrote no run summary.")


def test_each_failed_lane_adds_its_own_clause():
    coverage = {"functions": 1, "files": 1, "grade": "A",
                "lane_failures": {"py": "boom\nmore", "ts": "\n  bang  \n"}}

    assert builder().scored_line(coverage) == (
        "1 function in 1 file, 0 over the ceiling, CRAP load 0, grade A; lane 'py' failed: boom; "
        "lane 'ts' failed: bang.")


_NO_SUMMARY = ("it printed no run summary, so it crashed or was killed before scoring; "
               "its error is in the job log")


@pytest.mark.parametrize("coverage, reason", [
    (None, _NO_SUMMARY),
    ({}, _NO_SUMMARY),
    ({"functions": 3}, "the summary names no failed lane; read the job log"),
])
def test_a_failed_coverage_names_what_it_can(coverage, reason):
    assert builder().coverage_failure(coverage) == reason


def test_a_failed_verify_with_no_findings_listed_reads_zero_of_each():
    """No item fails with the exit, so the phrase has no label to name, and no
    run_id means no run was measured."""
    assert builder().verdict_line({"ok": False}, 6) == (
        "**verify failed, exit 6.** Nothing was measured against the baseline file at ?: "
        "0 gate violations, 0 ratchet regressions, 0 new test failures, 0 uncovered changed lines.")


def test_a_finding_s_missing_fields_read_as_dashes_and_the_cut_counts_what_it_hid():
    verify = {"ok": False, "run_id": 4, "baseline_run": 3, "changed_files": 1,
              "findings": [{"kind": "gate_violation", "exit_code": 6, "rule": "complexity gate",
                            "path": "a.py", "start": 2, "long_name": "f( )", "ccn": 9, "cov": 0.8,
                            "remedy": "decompose"},
                           {"kind": "diff_uncovered", "path": "a.py", "line": 3}],
              "counts": {"diff_uncovered_count": 2}}

    assert builder().verdict_line(verify, 6).splitlines() == [
        "**verify failed, exit 6: complexity gate.**", "",
        "- gate: `a.py:2` `f( )` ccn 9, cov 80%, crap - -> decompose",
        "- uncovered lines in `a.py`: 3",
        "- and 1 more uncovered changed line", "",
        "Run 4 against baseline 3, 1 changed file: 1 gate violation, 0 ratchet regressions, "
        "0 new test failures, 2 uncovered changed lines."]


@pytest.mark.parametrize("text, first", [(None, ""), ("  \n \n", ""), ("\n x \ny", "x")])
def test_the_first_line_of_nothing_is_empty(text, first):
    assert builder()._first_line(text) == first


def test_a_cell_escapes_what_would_break_the_table_or_the_code_span():
    assert builder()._cell_text("a|b`c\nd") == "a\\|b\\u0060c\\nd"


WORKLIST = {"active": [
    {"path": "a.py", "start": 1, "function": "f( )", "ccn": 9, "risk": 3.0},
    {"path": "b.py", "start": 5, "function": "g( )", "ccn": 7, "risk": 2.0, "remedy": "add-tests"},
    {"path": "c.py", "start": 9, "function": "h( )", "ccn": 6, "risk": 1.0, "remedy": "ok"},
]}


def test_the_comment_keeps_the_changed_files_rows_capped_at_top():
    """Called without coverage_exit, coverage ran: verify's line follows."""
    text = builder().body({"grade": "A"}, {"ok": True, "run_id": 2, "baseline_run": 1},
                          0, WORKLIST, ["b.py", "c.py"], 1)

    assert text.splitlines()[6:] == [
        "**verify passed.** Run 2 against baseline 1, 0 changed files.", "",
        "### Worklist: 2 changed files", "",
        "| File | Function | ccn | risk | remedy |", "|---|---|---:|---:|---|",
        "| `b.py:5` | `g( )` | 7 | 2.0 | add-tests |"]


def test_a_row_with_no_remedy_reads_a_dash():
    assert builder().table(WORKLIST["active"][:1]).splitlines()[-1] == "| `a.py:1` | `f( )` | 9 | 3.0 | - |"


def test_no_ranked_row_says_so():
    assert builder().table([]) == "No ranked function in these files."


def test_the_cli_reads_nul_records_and_writes_utf8_with_lf(tmp_path):
    (tmp_path / "w.json").write_text(json.dumps(WORKLIST), encoding="utf-8")
    (tmp_path / "changed.z").write_bytes("b.py\0a.py\0é.py\0".encode("utf-8"))
    out, js = tmp_path / "c.md", tmp_path / "c.json"

    assert builder().main(["--worklist", str(tmp_path / "w.json"), "--changed-z",
                           str(tmp_path / "changed.z"), "--top", "1", "--out", str(out),
                           "--json-out", str(js)]) == 0
    text = out.read_bytes().decode("utf-8")
    assert ("### Worklist: 3 changed files" in text, "`a.py:1`" in text, "`b.py:5`" in text,
            "\r" in text, js.read_bytes().count(b"\r")) == (True, True, False, False, 0)


def test_the_cli_with_no_changed_list_ranks_the_whole_repository(tmp_path):
    (tmp_path / "w.json").write_text(json.dumps(WORKLIST), encoding="utf-8")
    out = tmp_path / "c.md"

    builder().main(["--worklist", str(tmp_path / "w.json"), "--out", str(out)])

    assert "### Worklist: the whole repository, top 3" in out.read_text(encoding="utf-8")


def test_a_changed_list_file_reads_windows_separators_as_slashes(tmp_path):
    changed = tmp_path / "changed.txt"
    changed.write_text("src\\a.py\n\n  b.py \n", encoding="utf-8")

    assert builder()._read_lines(str(changed)) == ["src/a.py", "b.py"]


def test_the_options_say_what_each_file_holds(monkeypatch):
    """The parser the action calls, read field by field: what `--help` prints."""
    parsers = []

    def parse(self, argv=None):
        parsers.append(self)
        return argparse.Namespace(out="comment.md", changed_line=False)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse)
    builder()._parse([])
    (parser,) = parsers

    assert parser.description == "Render one pull-request comment out of crapkit's own JSON payloads."
    assert [(a.option_strings[-1], a.default, a.type, a.required, a.help)
            for a in parser._actions[1:]] == [
        ("--coverage", None, None, False, "crapkit coverage --json output"),
        ("--coverage-exit", 0, int, False,
         "coverage's exit code; non-zero means verify was not run"),
        ("--verify", None, None, False, "crapkit verify --json output"),
        ("--verify-exit", 0, int, False, "verify's exit code"),
        ("--base-sha", None, None, False,
         "file holding the fork point the base run was made at; empty or missing when it was not"),
        ("--base-reason", None, None, False, "file holding why the base run was not made"),
        ("--worklist", None, None, False, "crapkit worklist --json output"),
        ("--changed", None, None, False, "file holding one changed path per line"),
        ("--changed-z", None, None, False, "file holding NUL-separated Git paths"),
        ("--changed-error", None, None, False,
         "file holding git's error when the base diff failed; empty or missing when it ran"),
        ("--top", "5", None, False,
         "rows to render (default 5); a value that is not a whole number warns and renders 5"),
        ("--out", None, None, False, "where to write the markdown; required unless --changed-line"),
        ("--json-out", None, None, False, "where to write the {\"body\": ...} gh api sends"),
        ("--changed-line", False, None, False,
         "print the changed-files step's log line for --changed-z and write nothing"),
    ]
