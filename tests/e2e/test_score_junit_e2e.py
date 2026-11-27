"""`crapkit score --junit FILE` end to end: the CLI run on a committed repo
whose lcov tracefile sits beside its source, as CI leaves one.

wide (ccn 31) runs every line and scores CRAP 31; five (ccn 5) runs none and
scores 30; pick (ccn 3) runs 4 of 6 lines and scores 3.33. Each case parses the
report with ElementTree and reads it back through crapkit's own JUnit reader.
"""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo
from crapkit.junitparse import suite_summary

run_cli = cli_runner(encoding="utf-8", errors="replace")

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


def _repo(tmp_path: Path, folder: str = "src") -> Path:
    hits = {f"{folder}/calc.py": {1: 1, 2: 1, 3: 0, 4: 1, 5: 0, 6: 1, **dict.fromkeys(range(9, 19), 0)},
            f"{folder}/wide.py": dict.fromkeys(range(1, 63), 1)}
    lcov = "TN:\n" + "".join(f"SF:{path}\n" + "".join(f"DA:{n},{hit}\n" for n, hit in lines.items())
                             + "end_of_record\n" for path, lines in hits.items())
    repo = tmp_path / "repo"
    for rel, text in {f"{folder}/calc.py": CALC, f"{folder}/wide.py": WIDE, "lcov.info": lcov}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "source and the tracefile CI wrote")
    return repo


def _untracked(repo: Path) -> list[str]:
    return subprocess.run(["git", "ls-files", "--others", "-z"], cwd=repo, check=True,
                          capture_output=True).stdout.decode("utf-8").split("\0")[:-1]


def _cases(report: Path) -> tuple[ET.Element, dict[tuple[str, str], str | None]]:
    """The suite, and each case's (classname, name) with its failure message."""
    root = ET.fromstring(report.read_bytes())
    [suite] = root.iter("testsuite")
    cases = {}
    for case in suite.iter("testcase"):
        failure = case.find("failure")
        cases[case.get("classname"), case.get("name")] = None if failure is None else failure.get("message")
    return suite, cases


def test_fail_above_exits_6_with_one_case_per_function_and_one_failure(tmp_path):
    repo = _repo(tmp_path)
    res = run_cli(repo, "score", "src", "--coverage", "lcov.info", "--fail-above", "30", "--junit", "out.xml")

    assert res.returncode == 6, res.stderr
    assert res.stdout.splitlines() == ["crap    31.00  ccn  31  cov 100%  src/wide.py:1  wide( x )",
                                       "3 functions, 1 over 30"]
    assert _untracked(repo) == ["out.xml"]
    suite, cases = _cases(repo / "out.xml")
    assert cases == {("src/calc.py", "pick( a , b )"): None,
                     ("src/calc.py", "five( a , b , c , d )"): None,
                     ("src/wide.py", "wide( x )"): "CRAP 31.0 over 30 (ccn 31, cov 100%)"}
    assert (suite.get("tests"), suite.get("failures")) == ("3", "1")
    assert suite_summary((repo / "out.xml").read_bytes()) == (
        {"src/wide.py::wide( x )"}, {"tests": 3, "skipped": 0})


def test_without_fail_above_the_failures_are_the_functions_the_text_lists(tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "reports" / "out.xml"
    res = run_cli(repo, "score", "src", "--coverage", "lcov.info", "--junit", str(out))

    assert res.returncode == 0, res.stderr
    *listed, total = res.stdout.splitlines()
    assert total == "3 functions, 2 over 6"
    suite, cases = _cases(out)
    failing = {name for (_, name), message in cases.items() if message}
    assert failing == {line.split("  ")[-1] for line in listed} == {"wide( x )", "five( a , b , c , d )"}
    assert (suite.get("tests"), suite.get("failures")) == ("3", "2")
    assert _untracked(repo) == []


def test_a_path_with_a_space_an_accent_and_an_ampersand_round_trips(tmp_path):
    folder = "my code é & co"
    repo = _repo(tmp_path, folder)
    res = run_cli(repo, "score", folder, "--coverage", "lcov.info", "--junit", "out é & co.xml")

    assert res.returncode == 0, res.stderr
    report = repo / "out é & co.xml"
    assert b"my code \xc3\xa9 &amp; co/wide.py" in report.read_bytes()
    _, cases = _cases(report)
    assert sorted(cases) == [(f"{folder}/calc.py", "five( a , b , c , d )"),
                             (f"{folder}/calc.py", "pick( a , b )"), (f"{folder}/wide.py", "wide( x )")]
    assert suite_summary(report.read_bytes())[0] == {f"{folder}/calc.py::five( a , b , c , d )",
                                                     f"{folder}/wide.py::wide( x )"}


@pytest.mark.parametrize("flag", ["--junit", "--sarif"])
def test_a_directory_junit_path_refuses_as_sarif_does_before_any_analysis(tmp_path, flag):
    """The coverage file is missing, so a refusal after the format sniff would
    name --coverage. `coverage --sarif` gives the same words for its flag."""
    repo = _repo(tmp_path)
    (repo / "reports").mkdir()
    (repo / "crapkit.toml").write_text('[[scope]]\nname = "py"\npaths = ["src"]\nlanguages = ["python"]\n'
                                       '[[lane]]\nname = "unit"\ncommand = "true"\n'
                                       'artifact = "lcov.info"\nparser = "lcov"\nscopes = ["py"]\n',
                                       encoding="utf-8")
    argv = (["score", "src", "--coverage", "missing.info", "--junit", "reports"] if flag == "--junit"
            else ["coverage", "--reuse-artifacts", "--sarif", "reports"])
    res = run_cli(repo, *argv)

    assert res.returncode == 3, res.stderr
    assert f"{flag} 'reports' is a directory; name a file to write" in res.stderr
    assert not (repo / ".crapkit").exists()
