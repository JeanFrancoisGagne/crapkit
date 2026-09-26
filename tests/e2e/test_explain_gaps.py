"""explain --tests against real context data, and istanbul branch attribution.

Everything asserts through a public seam: the CLI as a subprocess on a tmp_path
repo built inline, or the file reader other tools call.
explain never runs a lane; it reads what a lane left on disk, and it names test
ids only off an artifact whose stamp says its line numbers still point at the
file. A test that wants ids listed has `crapkit coverage` run a stub lane that
writes the contexts the test chose. A hand-written artifact carries no stamp,
so the tests that write one pin what explain says in its place.
"""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from crapkit.coverage_istanbul import parse_istanbul_both_file

from conftest import cli_runner, git_commit_all, git_init_repo

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
command = "python write_cov.py"
artifact = "coverage-py.json"
parser = "coveragepy"
scopes = ["py"]
"""

ISTANBUL_LANE = """
[[lane]]
name = "web"
command = "python write_web.py"
artifact = "web-coverage.json"
parser = "istanbul"
scopes = ["py"]
"""

# The py lane: every function in pylib fully run, plus the per-line contexts
# the test left in contexts.json.
WRITE_COV = '''import ast, json, pathlib

files = {}
for path in sorted(pathlib.Path("pylib").rglob("*.py")):
    functions = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef):
            lines = list(range(node.lineno, node.end_lineno + 1))
            functions[node.name] = {"start_line": node.lineno, "executed_lines": lines,
                                    "missing_lines": [],
                                    "summary": {"covered_lines": len(lines),
                                                "num_statements": len(lines),
                                                "num_branches": 0, "covered_branches": 0}}
    files[path.as_posix()] = {"functions": functions, "missing_lines": []}
for rel, entry in json.loads(pathlib.Path("contexts.json").read_text(encoding="utf-8")).items():
    files.setdefault(rel, {}).update(entry)
with open("coverage-py.json", "w", encoding="utf-8") as fh:
    json.dump({"meta": {"branch_coverage": True, "show_contexts": True}, "files": files}, fh)
'''

# The web lane: a valid istanbul report, which is no coverage.py report.
WRITE_WEB = '''import json

loc = {"start": {"line": 4, "column": 0}, "end": {"line": 7, "column": 13}}
report = {"pylib/mod.py": {"path": "pylib/mod.py", "statementMap": {"0": loc}, "s": {"0": 1},
                           "fnMap": {"0": {"name": "guarded", "decl": loc, "loc": loc}},
                           "f": {"0": 1}, "branchMap": {}, "b": {}}}
with open("web-coverage.json", "w", encoding="utf-8") as fh:
    json.dump(report, fh)
'''


run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


def make_repo(tmp_path: Path, config: str, module: str = MODULE) -> Path:
    repo = tmp_path / "consumer"
    (repo / "pylib").mkdir(parents=True)
    (repo / "pylib" / "mod.py").write_text(module, encoding="utf-8", newline="\n")
    (repo / "crapkit.toml").write_text(config, encoding="utf-8", newline="\n")
    for name, text in {"write_cov.py": WRITE_COV, "write_web.py": WRITE_WEB,
                       ".gitignore": ".crapkit/\n*.json\ninv.tsv\n"}.items():
        (repo / name).write_text(text, encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "init")
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
    """A hand-written artifact: no lane ran, so no stamp vouches for it."""
    report = {"meta": {"branch_coverage": True, "show_contexts": True}, "files": files}
    (repo / rel).write_text(json.dumps(report), encoding="utf-8", newline="\n")


def measure_contexts(repo: Path, files: dict) -> None:
    """`crapkit coverage` runs the stub lanes, which record FILES' contexts."""
    (repo / "contexts.json").write_text(json.dumps(files), encoding="utf-8", newline="\n")
    res = run_cli(repo, "coverage")
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.fixture()
def inventoried(tmp_path: Path):
    """A repo with one inventory run and the guarded span already read back."""
    repo = make_repo(tmp_path, BASE_CFG + PY_LANE)
    assert run_cli(repo, "inventory", "--export", "inv.tsv").returncode == 0
    start, end = span_of(repo, "pylib/mod.py", "guarded")
    return repo, start, end


def test_explain_tests_lists_the_covering_tests_sorted(inventoried):
    repo, start, end = inventoried
    measure_contexts(repo, {"pylib/mod.py": {"contexts": {
        str(start): ["tests/test_mod.py::test_beta|run", ""],
        str(end): ["tests/test_mod.py::test_alpha|run"],
        str(end + 40): ["tests/test_mod.py::test_far_away|run"],
    }}})

    res = run_cli(repo, "explain", "pylib/mod.py", "guarded", "--tests")

    assert res.returncode == 0, res.stderr
    assert "    covered by tests/test_mod.py::test_alpha" in res.stdout, res.stdout
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
    # An istanbul lane measured beside the py lane records no contexts. explain
    # asks each lane through its own format, so the py lane's ids still list;
    # test_coverage_format_adapters pins that the istanbul lane is never asked.
    repo = make_repo(tmp_path, BASE_CFG + ISTANBUL_LANE + PY_LANE)
    assert run_cli(repo, "inventory", "--export", "inv.tsv").returncode == 0
    start, end = span_of(repo, "pylib/mod.py", "guarded")
    measure_contexts(repo, {"pylib/mod.py": {"contexts": {
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
            "noloc": {"line": 95},
            "outside": {"loc": {"start": {"line": 90}}},
        },
        "b": {"inside": [3, 0], "noloc": [1, 1], "outside": [0, 0, 0]},
    }
}


def solo_of(artifact: dict):
    with TemporaryDirectory() as directory:
        path = Path(directory) / "coverage.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        per_file, _, _ = parse_istanbul_both_file(path, repo_root="C:/repo")
    (fn,) = per_file["web/solo.ts"]
    return fn


def test_istanbul_branches_outside_every_span_are_ignored():
    """`noloc` has no loc and sits on the `line` beside it, 95, outside solo
    too. A branch with neither refuses the artifact:
    tests/unit/test_coverage_istanbul.py::test_a_branch_with_neither_loc_nor_line_refuses_the_artifact_naming_it"""
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
