"""A Cobertura lane end to end, from a report gcovr 7.2 recorded.

The fixture under tests/fixtures/coverage/cobertura is a CMake repo and the
report gcovr wrote for it through init's gcovr stanza (its README names the
command). Each case builds a git repo around the fixture, declares a lane with
parser = "cobertura" whose command copies the report into place, and runs the
CLI: coverage exits 0, worklist shows cov and add-tests, brief lists the hits-0
lines, and explain --tests has no contexts to show. The <source> root is tried
as recorded (`.`) and absolute under the checkout; a root from another
checkout is refused with exit 5.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo
from crapkit.coverage_cobertura import WRONG_TREE_FIX

run_cli = cli_runner(encoding="utf-8", errors="replace")

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "cobertura"
RECORDED = (FIXTURE / "gcovr-7.2-stanza.xml").read_text(encoding="utf-8")
SOURCES = ("CMakeLists.txt", "src/shapes.cpp", "src/shapes.h", "tests/test_shapes.cpp")
SHAPES = "src/shapes.cpp"

# The lane's command: copy the report the test placed into the artifact path.
_COPY = ("from pathlib import Path\n"
         "Path('.crapkit/cov').mkdir(parents=True, exist_ok=True)\n"
         "Path('.crapkit/cov/cpp.xml').write_bytes(Path('report.xml').read_bytes())\n")
# grade's ccn is 7: under a ceiling of 8 no decomposition is owed, so the
# uncovered function's remedy is add-tests.
CONFIG = ("[crapkit]\ntarget = 8\n\n"
          '[[scope]]\nname = "core"\npaths = ["src"]\nlanguages = ["cpp"]\n\n'
          '[exclude]\nglobs = ["make_report.py", "tests/**"]\n\n'
          '[[lane]]\nname = "cpp"\ncommand = "python make_report.py"\n'
          'artifact = ".crapkit/cov/cpp.xml"\nparser = "cobertura"\nscopes = ["core"]\n')


def _repo(tmp_path: Path, source) -> Path:
    """The CMake fixture and the recorded report with its `.` source
    respelled by `source(repo)`."""
    repo = Path(os.path.realpath(tmp_path)) / "repo"
    for rel in SOURCES:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes((FIXTURE / "cmake-repo" / rel).read_bytes())
    files = {"crapkit.toml": CONFIG, "make_report.py": _COPY, ".gitignore": ".crapkit/\n",
             "report.xml": RECORDED.replace("<source>.</source>", f"<source>{source(repo)}</source>")}
    for rel, body in files.items():
        (repo / rel).write_text(body, encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "a C++ repo and its gcovr lane")
    return repo


SOURCE_ROOTS = {"recorded": lambda repo: ".", "absolute": lambda repo: repo.as_posix()}


@pytest.fixture(params=list(SOURCE_ROOTS))
def measured(request, tmp_path):
    """A repo whose Cobertura lane ran."""
    repo = _repo(tmp_path, SOURCE_ROOTS[request.param])
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["lane_failures"] == {}
    return repo


def _worklist(repo: Path) -> dict:
    """The functions over the ceiling, by handle."""
    res = run_cli(repo, "worklist", "--json")
    assert res.returncode == 0, res.stderr
    return {row["handle"]: row for row in json.loads(res.stdout)["active"]}


def test_worklist_reads_cov_from_the_report_and_hands_add_tests(measured):
    rows = _worklist(measured)

    assert rows["grade"]["path"] == SHAPES
    assert (rows["grade"]["cov"], rows["grade"]["remedy"]) == (0.0, "add-tests")
    assert rows["grade"]["flag"] == "measured"


def test_brief_lists_the_lines_with_hits_zero(measured):
    res = run_cli(measured, "brief", SHAPES, "grade", "--json")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["uncovered_lines"] == [*range(13, 26), 27]


def test_explain_tests_has_no_contexts_to_show(measured):
    res = run_cli(measured, "explain", SHAPES, "clamp", "--tests", "--json")

    assert res.returncode == 0, res.stderr
    (function,) = json.loads(res.stdout)["functions"]
    assert function["tests"] is None
    assert function["tests_note"].startswith("no context data")


def test_a_source_root_from_another_checkout_is_refused_with_exit_5(tmp_path):
    elsewhere = Path(os.path.realpath(tmp_path)) / "other"
    (elsewhere / "src").mkdir(parents=True)
    repo = _repo(tmp_path, lambda repo: elsewhere.as_posix())

    res = run_cli(repo, "coverage", "--json")

    assert res.returncode == 5, res.stdout + res.stderr
    assert "lane 'cpp' FAILED" in res.stderr
    assert WRONG_TREE_FIX in " ".join(res.stderr.split())
    assert f"{elsewhere.as_posix()}/{SHAPES}" in res.stderr
    assert json.loads(res.stdout)["error"]["exit"] == 5
