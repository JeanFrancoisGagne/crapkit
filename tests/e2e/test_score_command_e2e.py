"""`crapkit score` end to end, from coverage files committed beside the source.

One repo per format: the Rust crate and the tracefile cargo-llvm-cov 0.9.1
wrote for it (tests/fixtures/coverage/lcov), a coverage.py JSON report and an
istanbul JSON report written here. Each case runs the CLI with no crapkit.toml
and checks the exit, the lines, and that the work tree is byte-identical.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo
from crapkit import coverage_format

run_cli = cli_runner(encoding="utf-8", errors="replace")

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "lcov"

PY = """def pick(a, b):
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

JS = """function five(a, b, c, d) {
  if (a) return 1;
  if (b) return 2;
  if (c) return 3;
  if (d) return 4;
  return 0;
}
"""


def _summary(statements: int, covered: int) -> dict:
    return {"num_statements": statements, "covered_lines": covered, "excluded_lines": 0}


def _coveragepy(key: str) -> str:
    pick = {"start_line": 1, "executed_lines": [2, 4, 6], "missing_lines": [3, 5],
            "excluded_lines": [], "summary": _summary(5, 3)}
    five = {"start_line": 9, "executed_lines": [], "missing_lines": list(range(10, 19)),
            "excluded_lines": [], "summary": _summary(9, 0)}
    module = {"start_line": 1, "executed_lines": [1, 9], "missing_lines": [],
              "excluded_lines": [], "summary": _summary(2, 2)}
    files = {key: {"executed_lines": [1, 2, 4, 6, 9], "missing_lines": [3, 5, *range(10, 19)],
                   "excluded_lines": [], "functions": {"pick": pick, "five": five, "": module}}}
    return json.dumps({"meta": {"format": 3, "version": "7.13.1", "branch_coverage": False},
                       "files": files, "totals": {"covered_lines": 5}})


def _at(line: int, column: int) -> dict:
    return {"line": line, "column": column}


def _istanbul(key: str) -> str:
    ifs = range(2, 6)
    entry = {
        "path": key,
        "fnMap": {"0": {"name": "five", "decl": {"start": _at(1, 9), "end": _at(1, 13)},
                        "loc": {"start": _at(1, 26), "end": _at(7, 1)}, "line": 1}},
        "f": {"0": 0},
        "statementMap": {str(i): {"start": _at(line, 2), "end": _at(line, 18)}
                         for i, line in enumerate(range(2, 7))},
        "s": {str(i): 0 for i in range(5)},
        "branchMap": {str(i): {"loc": {"start": _at(line, 2), "end": _at(line, 18)}, "type": "if",
                               "line": line, "locations": [{"start": _at(line, 2), "end": _at(line, 18)},
                                                           {"start": _at(line, 2), "end": _at(line, 18)}]}
                      for i, line in enumerate(ifs)},
        "b": {str(i): [0, 0] for i in range(len(ifs))},
    }
    return json.dumps({key: entry})


def _repo(tmp_path: Path, files: dict[str, str | bytes], name: str = "repo") -> Path:
    """The files committed, each 10 s old: analysis keeps a file's stat only
    once it has held still 2 s, so a file written just now hides a write."""
    repo = Path(os.path.realpath(tmp_path)) / name
    past = time.time() - 10
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        path.write_bytes(data)
        os.utime(path, (past, past))
    git_init_repo(repo)
    git_commit_all(repo, "source and the coverage file CI wrote")
    return repo


def _rust(tmp_path: Path) -> Path:
    files: dict[str, str | bytes] = {f"rust/{p.name}": p.read_bytes() for p in (FIXTURE / "rust").iterdir()}
    files["lcov.info"] = (FIXTURE / "cargo-llvm-cov-0.9.1.lcov").read_bytes()
    return _repo(tmp_path, files)


def _status(repo: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=repo, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def _score(repo: Path, *args: str):
    before = _status(repo)
    res = run_cli(repo, "score", *args)
    assert _status(repo) == before == "", "score changed the work tree"
    assert not (repo / ".crapkit").exists()
    return res


def test_an_lcov_file_scores_with_no_config_and_fails_above_n(tmp_path):
    repo = _rust(tmp_path)
    res = _score(repo, "rust", "--coverage", "lcov.info")
    assert (res.returncode, res.stdout) == (0, "9 functions, 0 over 6\n"), res.stderr
    failing = _score(repo, "rust", "--coverage", "lcov.info", "--fail-above", "5")
    assert failing.returncode == 6, failing.stderr
    assert failing.stdout.splitlines() == [
        "crap     6.00  ccn   2  cov   0%  rust/shapes.rs:32  untouched x : i32",
        "9 functions, 1 over 5"]


def test_a_coveragepy_report_is_sniffed_and_scored(tmp_path):
    repo = _repo(tmp_path, {"src/calc.py": PY, "coverage.json": _coveragepy("src/calc.py")})
    res = _score(repo, "src", "--coverage", "coverage.json", "--fail-above", "29")
    assert res.returncode == 6, res.stderr
    assert res.stdout.splitlines() == [
        "crap    30.00  ccn   5  cov   0%  src/calc.py:9  five( a , b , c , d )",
        "2 functions, 1 over 29"]


def test_an_istanbul_report_is_sniffed_and_scored(tmp_path):
    repo = _repo(tmp_path, {"src/calc.js": JS, "coverage-final.json": _istanbul("src/calc.js")})
    res = _score(repo, "src", "--coverage", "coverage-final.json")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "crap    30.00  ccn   5  cov   0%  src/calc.js:1  five ( a , b , c , d )",
        "1 functions, 1 over 6"]


def test_format_overrides_the_sniff_and_an_unknown_file_exits_3(tmp_path):
    repo = _repo(tmp_path, {"src/calc.py": PY, "cov.data": _coveragepy("src/calc.py"),
                            "cov.xml": '<?xml version="1.0"?><report name="x"/>\n'})
    named = _score(repo, "src", "--coverage", "cov.data", "--format", "coveragepy")
    assert named.returncode == 0, named.stderr
    unknown = _score(repo, "src", "--coverage", "cov.xml")
    assert unknown.returncode == 3
    # Read off the registry, so a reader that lands later joins the list without a test edit.
    accepted = ", ".join(sorted(coverage_format._FORMATS))
    assert f"name it with --format. --format takes one of: {accepted}" in unknown.stderr


def test_a_tracefile_from_another_tree_exits_5(tmp_path):
    recorded = (FIXTURE / "cargo-llvm-cov-0.9.1.lcov").read_text(encoding="utf-8")
    files: dict[str, str | bytes] = {f"rust/{p.name}": p.read_bytes() for p in (FIXTURE / "rust").iterdir()}
    files["lcov.info"] = recorded.replace("SF:rust/", "SF:/home/user/other-checkout/rust/")
    repo = _repo(tmp_path, files)
    res = _score(repo, "rust", "--coverage", "lcov.info")
    assert res.returncode == 5
    assert "none of them a tracked source file under PATH rust" in res.stderr


def test_outside_a_git_repository_exits_4(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(PY, encoding="utf-8")
    (tmp_path / "coverage.json").write_text(_coveragepy("src/calc.py"), encoding="utf-8")
    assert run_cli(tmp_path, "score", "src", "--coverage", "coverage.json").returncode == 4


@pytest.mark.parametrize("folder", ["my code", "code été"])
def test_paths_with_a_space_and_non_ascii_letters(tmp_path, folder):
    key = f"{folder}/calc.py"
    repo = _repo(tmp_path, {key: PY, "cov dir/coverage.json": _coveragepy(key)},
                 name="the repo é")
    res = _score(repo, folder, "--coverage", "cov dir/coverage.json")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        f"crap    30.00  ccn   5  cov   0%  {key}:9  five( a , b , c , d )", "2 functions, 1 over 6"]
