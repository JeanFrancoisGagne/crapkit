"""explain --tests against real context data, and istanbul branch attribution.

Everything asserts through a public seam: the CLI as a subprocess on a tmp_path
repo built inline, or parse_istanbul, which is the parser other tools call.
The coverage artifacts are hand-written because explain never runs a lane; it
only reads what a lane left on disk.
"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit.coverage_istanbul import parse_istanbul

MODULE = '''"""Fixture module: one branchy function, one straight-line function."""


def guarded(a, b):
    if a and b:
        return [x for x in range(a) if x % 2]
    return []


def plain(a):
    return a
'''

RENAMED = '''"""Same file after guarded was renamed away."""


def latched(a, b):
    if a and b:
        return [x for x in range(a) if x % 2]
    return []


def plain(a):
    return a
'''

BASE_CFG = """[crapkit]
target = 6

[[scope]]
name = "py"
paths = ["pylib"]
languages = ["python"]
"""

PY_LANE = """
[[lane]]
name = "py"
command = "python --version"
artifact = "coverage-py.json"
parser = "coveragepy"
scopes = ["py"]
"""

ISTANBUL_LANE = """
[[lane]]
name = "web"
command = "python --version"
artifact = "web-coverage.json"
parser = "istanbul"
scopes = ["py"]
"""


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "crapkit", *args],
        cwd=repo, capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace", env=dict(os.environ),
    )


def make_repo(tmp_path: Path, config: str, module: str = MODULE) -> Path:
    repo = tmp_path / "consumer"
    (repo / "pylib").mkdir(parents=True)
    (repo / "pylib" / "mod.py").write_text(module, encoding="utf-8", newline="\n")
    (repo / "crapkit.toml").write_text(config, encoding="utf-8", newline="\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo


def span_of(repo: Path, path: str, fragment: str) -> tuple[int, int]:
    """The inventory export is the only public place a function's span is written."""
    text = (repo / "inv.tsv").read_text(encoding="utf-8")
    header, *rows = text.strip().splitlines()
    cols = header.split("\t")
    for row in rows:
        vals = dict(zip(cols, row.split("\t")))
        if vals["path"] == path and fragment in vals["long_name"]:
            return int(vals["start"]), int(vals["end"])
    raise AssertionError(f"no {fragment!r} row in:\n{text}")


def write_contexts(repo: Path, rel: str, files: dict) -> None:
    report = {"meta": {"branch_coverage": True, "show_contexts": True}, "files": files}
    (repo / rel).write_text(json.dumps(report), encoding="utf-8", newline="\n")


@pytest.fixture()
def inventoried(tmp_path: Path):
    """A repo with one inventory run and the guarded span already read back."""
    repo = make_repo(tmp_path, BASE_CFG + PY_LANE)
    assert run_cli(repo, "inventory", "--export", "inv.tsv").returncode == 0
    start, end = span_of(repo, "pylib/mod.py", "guarded")
    return repo, start, end


def test_explain_tests_lists_the_covering_tests_sorted(inventoried):
    repo, start, end = inventoried
    write_contexts(repo, "coverage-py.json", {"pylib/mod.py": {"contexts": {
        str(start): ["tests/test_mod.py::test_beta|run", ""],
        str(end): ["tests/test_mod.py::test_alpha|run"],
        str(end + 40): ["tests/test_mod.py::test_far_away|run"],
    }}})

    res = run_cli(repo, "explain", "pylib/mod.py", "guarded", "--tests")

    assert res.returncode == 0, res.stderr
    assert "    covered by tests/test_mod.py::test_alpha" in res.stdout
    assert "    covered by tests/test_mod.py::test_beta" in res.stdout
    assert res.stdout.index("test_alpha") < res.stdout.index("test_beta"), "listed sorted"
    assert "test_far_away" not in res.stdout, "a hit past the span belongs to another function"
    assert "no context data" not in res.stdout


def test_explain_tests_ignores_context_lines_for_other_files(inventoried):
    repo, start, end = inventoried
    write_contexts(repo, "coverage-py.json", {"pylib/other.py": {"contexts": {
        str(start): ["tests/test_other.py::test_other|run"],
    }}})

    res = run_cli(repo, "explain", "pylib/mod.py", "guarded", "--tests")

    assert res.returncode == 0, res.stderr
    assert "test_other" not in res.stdout
    assert "tests: no context data" in res.stdout
    assert "dynamic_context" in res.stdout


def test_explain_tests_without_any_artifact_prints_the_guidance_line(inventoried):
    repo, _start, _end = inventoried
    assert not (repo / "coverage-py.json").exists()

    res = run_cli(repo, "explain", "pylib/mod.py", "guarded", "--tests")

    assert res.returncode == 0, res.stderr
    assert "tests: no context data" in res.stdout
    assert "covered by" not in res.stdout


def test_explain_tests_skips_lanes_that_are_not_coveragepy(tmp_path: Path):
    # The istanbul lane's artifact is unparseable as a coverage.py report: if the
    # parser check did not skip it first, explain would die at exit 5.
    repo = make_repo(tmp_path, BASE_CFG + ISTANBUL_LANE + PY_LANE)
    assert run_cli(repo, "inventory", "--export", "inv.tsv").returncode == 0
    start, end = span_of(repo, "pylib/mod.py", "guarded")
    (repo / "web-coverage.json").write_text("{ not json at all", encoding="utf-8", newline="\n")
    write_contexts(repo, "coverage-py.json", {"pylib/mod.py": {"contexts": {
        str(start): ["tests/test_mod.py::test_guarded|run"],
    }}})

    res = run_cli(repo, "explain", "pylib/mod.py", "guarded", "--tests")

    assert res.returncode == 0, (res.returncode, res.stderr)
    assert "    covered by tests/test_mod.py::test_guarded" in res.stdout


def test_explain_says_a_function_absent_from_the_latest_run_has_no_span(tmp_path: Path):
    repo = make_repo(tmp_path, BASE_CFG + PY_LANE)
    assert run_cli(repo, "inventory", "--export", "inv.tsv").returncode == 0
    start, _end = span_of(repo, "pylib/mod.py", "guarded")
    write_contexts(repo, "coverage-py.json", {"pylib/mod.py": {"contexts": {
        str(start): ["tests/test_mod.py::test_guarded|run"],
    }}})
    (repo / "pylib" / "mod.py").write_text(RENAMED, encoding="utf-8", newline="\n")
    assert run_cli(repo, "inventory").returncode == 0

    res = run_cli(repo, "explain", "pylib/mod.py", "guarded", "--history", "--tests")

    assert res.returncode == 0, res.stderr
    assert "guarded" in res.stdout, "the older run still carries the trajectory"
    assert "commits: function not in the latest run" in res.stdout
    assert "covered by" not in res.stdout, "no span means no test list, artifact or not"
    assert "no context data" not in res.stdout, "silence, not guidance for a gone function"


ISTANBUL = {
    "C:/repo/web/solo.ts": {
        "fnMap": {"0": {"name": "solo", "decl": {"start": {"line": 10}},
                        "loc": {"start": {"line": 10}, "end": {"line": 20}}}},
        "f": {"0": 4},
        "branchMap": {
            "inside": {"loc": {"start": {"line": 12}}},
            "noloc": {},
            "outside": {"loc": {"start": {"line": 90}}},
        },
        "b": {"inside": [3, 0], "noloc": [1, 1], "outside": [0, 0, 0]},
    }
}


def solo_of(artifact: dict):
    per_file = parse_istanbul(json.dumps(artifact), repo_root="C:/repo")
    (fn,) = per_file["web/solo.ts"]
    return fn


def test_istanbul_branches_without_a_loc_line_and_outside_every_span_are_ignored():
    fn = solo_of(ISTANBUL)
    assert (fn.branches_total, fn.branches_covered) == (2, 1), \
        "only the line-12 arms belong to solo"
    assert fn.coverage == 0.5


def test_istanbul_counts_the_same_branches_once_their_lines_land_in_the_span():
    placed = copy.deepcopy(ISTANBUL)
    cov = placed["C:/repo/web/solo.ts"]
    cov["branchMap"]["noloc"] = {"loc": {"start": {"line": 13}}}
    cov["branchMap"]["outside"] = {"loc": {"start": {"line": 14}}}

    fn = solo_of(placed)

    assert (fn.branches_total, fn.branches_covered) == (7, 3), \
        "the skipped arms are real data, dropped only for want of a line inside the span"
