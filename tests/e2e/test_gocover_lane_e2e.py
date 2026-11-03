"""A Go coverprofile lane end to end, from a profile Go 1.26.8 recorded.

The fixture under tests/fixtures/coverage/coverprofile is a Go module and the
profile `go test` wrote for it through init's go stanza (its README names the
command). Each case builds a git repo around the fixture, declares a lane with
parser = "coverprofile" whose command copies the profile into place, and runs
the CLI: coverage exits 0, worklist shows cov and add-tests, brief lists the
lines no block ran, and explain --tests has no context data.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner(encoding="utf-8", errors="replace")

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "coverprofile"
SHAPES = "shapes.go"
CALC = "internal/calc/calc.go"

# The lane's command: copy the profile the test placed into the artifact path.
_COPY = ("from pathlib import Path\n"
         "Path('.crapkit/cov/go.out').write_bytes(Path('profile.out').read_bytes())\n")
# Under a ceiling of 4 Unused (ccn 2) and Mul (ccn 3) owe no decomposition, so
# a function no test runs gets add-tests.
CONFIG = ("[crapkit]\ntarget = 4\n\n"
          '[[scope]]\nname = "go"\npaths = ["."]\nlanguages = ["go"]\n\n'
          '[exclude]\nglobs = ["make_profile.py", "**/*_test.go"]\n\n'
          '[[lane]]\nname = "go"\ncommand = "python make_profile.py"\n'
          'artifact = ".crapkit/cov/go.out"\nparser = "coverprofile"\nscopes = ["go"]\n')


@pytest.fixture
def measured(tmp_path):
    """A repo whose coverprofile lane ran over the recorded set profile."""
    repo = Path(os.path.realpath(tmp_path)) / "repo"
    shutil.copytree(FIXTURE / "go-repo", repo)
    (repo / "profile.out").write_bytes((FIXTURE / "go-1.26.8-set.out").read_bytes())
    files = {"crapkit.toml": CONFIG, "make_profile.py": _COPY, ".gitignore": ".crapkit/\n"}
    for rel, body in files.items():
        (repo / rel).write_text(body, encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "a Go module and its coverprofile lane")
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["lane_failures"] == {}
    return repo


def _worklist(repo: Path) -> dict:
    """The functions over the ceiling, by handle."""
    res = run_cli(repo, "worklist", "--json")
    assert res.returncode == 0, res.stderr
    return {row["handle"]: row for row in json.loads(res.stdout)["active"]}


def test_worklist_reads_cov_from_the_profile_and_hands_add_tests(measured):
    rows = _worklist(measured)

    assert {name: (row["path"], row["cov"], row["remedy"], row["flag"])
            for name, row in rows.items()} == {
        "Unused": (SHAPES, 0.0, "add-tests", "measured"),
        "Mul": (CALC, 0.0, "add-tests", "measured")}


def test_brief_lists_the_lines_no_block_ran(measured):
    res = run_cli(measured, "brief", SHAPES, "Unused", "--json")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["uncovered_lines"] == [50, 51, 52, 53, 54]


def test_explain_tests_has_no_context_data_for_a_go_function(measured):
    res = run_cli(measured, "explain", SHAPES, "Classify", "--tests", "--json")

    assert res.returncode == 0, res.stderr
    (function,) = json.loads(res.stdout)["functions"]
    assert function["tests"] is None
    assert function["tests_note"].startswith("no context data")
