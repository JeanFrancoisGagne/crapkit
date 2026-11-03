"""`crapkit score --junit FILE`: the JUnit writer on hand-built rows, and the
command driven in process over a small committed repo.

The repo's numbers are counted by hand: pick (ccn 3) runs 4 of its 6 lines,
CRAP 3.33; five (ccn 5) runs none of its 10, CRAP 30; wide (ccn 31) runs every
line, CRAP 31. Over 30 only wide fails; over the default ceiling of 6, five and
wide do.
"""
from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from crapkit import __version__, junit_report
from crapkit.cli import main
from crapkit.cli._shared import _refuse_unwritable_outputs
from crapkit.errors import ConfigError
from crapkit.junitparse import suite_summary
from crapkit.score import ScoredRow, crap

CALC = """def pick(a, b):
    if a:
        return 1
    if b:
        return 2
    return 0


def five(a, b, c, d):
    if a:
        return 1
    if b:
        return 2
    if c:
        return 3
    if d:
        return 4
    return 0
"""
WIDE = "def wide(x):\n" + "".join(f"    if x == {k}:\n        return {k}\n" for k in range(30)) \
    + "    return -1\n"
HITS = {"src/calc.py": {1: 1, 2: 1, 3: 0, 4: 1, 5: 0, 6: 1, **{line: 0 for line in range(9, 19)}},
        "src/wide.py": {line: 1 for line in range(1, 63)}}


def _row(path: str, long_name: str, ccn: int, cov: float, start: int = 1) -> ScoredRow:
    return ScoredRow("python", path, long_name, start, start + 9, ccn, ccn, ccn, 10, 1, 1,
                     cov, "", crap(ccn, cov), "ok")


ROWS = [_row("src/wide.py", "wide( x )", 31, 1.0), _row("src/calc.py", "five( a , b , c , d )", 5, 0.0, 9),
        _row("src/calc.py", "pick( a , b )", 3, 4 / 6)]


def _suite(data: bytes) -> ET.Element:
    root = ET.fromstring(data)
    assert root.tag == "testsuites"
    [suite] = root
    return suite


def _failures(suite: ET.Element) -> dict[str, str]:
    return {case.get("name"): failure.get("message")
            for case in suite.iter("testcase") for failure in case.iter("failure")}


# --- the writer --------------------------------------------------------------------


def test_one_case_per_row_and_one_failure_over_the_limit():
    suite = _suite(junit_report.report_bytes(ROWS, 30))

    assert (suite.tag, suite.get("name"), suite.get("tests"), suite.get("failures")) == \
        ("testsuite", "crapkit", "3", "1")
    assert [(case.get("classname"), case.get("name"), case.get("file"))
            for case in suite.iter("testcase")] == [
        ("src/calc.py", "pick( a , b )", "src/calc.py"),
        ("src/calc.py", "five( a , b , c , d )", "src/calc.py"),
        ("src/wide.py", "wide( x )", "src/wide.py")]
    assert _failures(suite) == {"wide( x )": "CRAP 31.0 over 30 (ccn 31, cov 100%)"}


def test_the_failures_are_the_rows_over_the_limit_and_a_score_at_it_passes():
    """CRAP(18, 2/3) is 30 exactly and its double 30.000000000000004: at the
    limit, as the text report decides it, so it passes."""
    at_thirty = _row("src/edge.py", "edge( x )", 18, 2 / 3)
    suite = _suite(junit_report.report_bytes([*ROWS, at_thirty], 6))

    assert _failures(suite) == {"wide( x )": "CRAP 31.0 over 6 (ccn 31, cov 100%)",
                                "five( a , b , c , d )": "CRAP 30.0 over 6 (ccn 5, cov 0%)",
                                "edge( x )": "CRAP 30.0 over 6 (ccn 18, cov 67%)"}
    assert _failures(_suite(junit_report.report_bytes([at_thirty], 30))) == {}
    assert suite.get("failures") == "3"


def test_the_properties_name_the_version_and_the_formula():
    properties = _suite(junit_report.report_bytes(ROWS, 30)).find("properties")

    assert {p.get("name"): p.get("value") for p in properties} == {
        "version": __version__, "formula": "CRAP = ccn^2 * (1 - cov)^3 + ccn"}


def test_crapkits_own_junit_reader_counts_the_same_tests_and_failures():
    failed, counts = suite_summary(junit_report.report_bytes(ROWS, 6))

    assert failed == {"src/wide.py::wide( x )", "src/calc.py::five( a , b , c , d )"}
    assert counts == {"tests": 3, "skipped": 0}


def test_a_path_and_name_with_xml_special_characters_and_non_ascii_letters_round_trip():
    path, name = 'my code/café & <co>/"x".py', "f( a = '<&>' , b )"
    data = junit_report.report_bytes([_row(path, name, 7, 0.0)], 6)
    [case] = _suite(data).iter("testcase")

    assert data.startswith(b"<?xml version='1.0' encoding='utf-8'?>\n")
    assert "café".encode() in data
    assert (case.get("classname"), case.get("name"), case.get("file")) == (path, name, path)
    assert suite_summary(data)[0] == {f"{path}::{name}"}


def test_twins_in_one_file_take_their_ratchet_key_names_so_each_case_counts():
    """Python method long names carry no class, so two classes' __init__
    collide; a reader that keys cases by name would keep only the first."""
    init = "__init__( self )"
    data = junit_report.report_bytes([_row("src/shapes.py", init, 7, 0.0, 12),
                                      _row("src/shapes.py", init, 7, 0.0, 3)], 6)

    assert [case.get("name") for case in _suite(data).iter("testcase")] == [init, f"{init}#2"]
    assert suite_summary(data)[0] == {f"src/shapes.py::{init}", f"src/shapes.py::{init}#2"}


def test_write_replaces_the_file_whole_and_leaves_no_temporary_file(tmp_path):
    out = tmp_path / "out.xml"
    out.write_bytes(b"an older report")
    junit_report.write(out, ROWS, 30)

    assert out.read_bytes() == junit_report.report_bytes(ROWS, 30)
    assert [p.name for p in tmp_path.iterdir()] == ["out.xml"]


def test_a_write_that_fails_keeps_the_old_file(tmp_path, monkeypatch):
    out = tmp_path / "out.xml"
    out.write_bytes(b"an older report")

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        junit_report.write(out, ROWS, 30)
    assert out.read_bytes() == b"an older report"
    assert [p.name for p in tmp_path.iterdir()] == ["out.xml"]


# --- the command -------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                           "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                          cwd=root, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def _lcov() -> str:
    return "TN:\n" + "".join(f"SF:{path}\n" + "".join(f"DA:{line},{hit}\n" for line, hit in sorted(lines.items()))
                             + "end_of_record\n" for path, lines in HITS.items())


def _repo(root: Path) -> Path:
    for rel, text in {"src/calc.py": CALC, "src/wide.py": WIDE, "lcov.info": _lcov()}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def _score(capsys, repo: Path, *args: str) -> tuple[int, list[str], str]:
    code = main(["score", "src", "--coverage", "lcov.info", "--repo", str(repo), *args])
    out, err = capsys.readouterr()
    return code, out.splitlines(), err


def test_fail_above_exits_6_and_writes_one_case_per_function_and_one_failure(tmp_path, capsys):
    repo = _repo(tmp_path / "r")
    code, lines, err = _score(capsys, repo, "--fail-above", "30", "--junit", "out.xml")

    assert (code, lines) == (6, ["crap    31.00  ccn  31  cov 100%  src/wide.py:1  wide( x )",
                                 "3 functions, 1 over 30"]), err
    suite = _suite((repo / "out.xml").read_bytes())
    assert (suite.get("tests"), suite.get("failures")) == ("3", "1")
    assert _failures(suite) == {"wide( x )": "CRAP 31.0 over 30 (ccn 31, cov 100%)"}


def test_without_fail_above_the_failures_are_the_lines_the_text_lists_and_the_exit_stays_0(
        tmp_path, capsys):
    repo = _repo(tmp_path / "r")
    out = tmp_path / "reports" / "out.xml"
    code, lines, err = _score(capsys, repo, "--junit", str(out))

    assert code == 0, err
    listed = {line.split("  ")[-1] for line in lines[:-1]}
    assert listed == set(_failures(_suite(out.read_bytes()))) == {"wide( x )", "five( a , b , c , d )"}
    assert lines[-1] == "3 functions, 2 over 6"


@pytest.mark.parametrize("named", ["out", "../out.xml"])
def test_an_unwritable_junit_path_refuses_as_sarif_does_before_any_analysis(tmp_path, capsys, named):
    """The coverage file is missing, so a refusal that came after the format
    sniff would name --coverage instead."""
    repo = _repo(tmp_path / "r")
    (repo / "out").mkdir()
    with pytest.raises(ConfigError) as sarif:
        _refuse_unwritable_outputs(repo.resolve(), {"--sarif": named})
    code = main(["score", "src", "--coverage", "missing.info", "--repo", str(repo), "--junit", named])

    assert code == 3
    assert str(sarif.value).replace("--sarif", "--junit") in capsys.readouterr().err
    assert not (tmp_path / "out.xml").exists()
