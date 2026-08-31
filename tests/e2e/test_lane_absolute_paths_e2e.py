"""End-to-end: this tree, reported in absolute paths, is not "another tree".

A runner can be told to write absolute paths, and coverage.py under
`relative_files = false` (its default under some layouts) does exactly that for
this checkout's own files. The join is root-relative, so the artifact still
matches nothing and the lane still fails — but the cause is the runner's path
spelling, and the refusal that named venvs and `path_prefix` sent a reader
hunting for a second checkout that was never there.

Real git, real CLI. The coveragepy lane writes its artifact from a script, so
the absolute paths are exact and the test stays hermetic.
"""
import json
import shutil
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")

# A coverage.py JSON report about THIS checkout's pylib/mod.py, spelled
# absolutely — what `relative_files = false` produces.
ABSOLUTE_COV = (
    "import json, os\n"
    "mod = os.path.join(os.getcwd(), 'pylib', 'mod.py')\n"
    "report = {'meta': {'branch_coverage': True}, 'files': {mod: {'functions': {\n"
    "    'classify': {'start_line': 1, 'executed_lines': [1, 2], 'missing_lines': [],\n"
    "                 'summary': {'covered_lines': 2, 'num_statements': 2,\n"
    "                             'num_branches': 2, 'covered_branches': 2}}}}}}\n"
    "open('coverage-py.json', 'w', encoding='utf-8').write(json.dumps(report))\n"
)

PY_LANE = """[[lane]]
name = "py"
command = "python make_abs_cov.py"
artifact = "coverage-py.json"
parser = "coveragepy"
scopes = ["py"]
full_suite = false
"""


def _swap_py_lane(config: str) -> str:
    """The fixture's real pytest lane, replaced by the artifact writer. It is the
    last block in the file, so truncating at it is the whole edit."""
    head, marker, _ = config.partition('[[lane]]\nname = "py"')
    assert marker, "the fixture no longer declares a lane named py"
    return head + PY_LANE


@pytest.fixture()
def absolute_paths_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    config = repo / "crapkit.toml"
    config.write_text(_swap_py_lane(config.read_text(encoding="utf-8")),
                      encoding="utf-8", newline="\n")
    (repo / "make_abs_cov.py").write_text(ABSOLUTE_COV, encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text(
        ".crapkit/\ncoverage/\ncoverage-py.json\njunit.xml\n__pycache__/\n",
        encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "init")
    return repo


def test_absolute_in_tree_paths_fail_the_lane_naming_the_runner_switch(absolute_paths_repo):
    res = run_cli(absolute_paths_repo, "coverage", "--json")

    assert res.returncode == 5, res.stderr
    assert "lane 'py' FAILED" in res.stderr
    assert "relative_files = true" in res.stderr
    assert "[tool.coverage.run]" in res.stderr and ".coveragerc" in res.stderr


def test_the_refusal_does_not_send_the_reader_after_another_checkout(absolute_paths_repo):
    """The bug. Every advice in the old message was about a tree that does not
    exist here: a venv bound elsewhere, and a prefix that only prepends."""
    err = run_cli(absolute_paths_repo, "coverage", "--json").stderr

    assert "different tree" not in err
    assert "uv run" not in err
    assert "path_prefix" not in err
    assert "under this checkout" in err


def test_the_scope_reads_as_a_tooling_gap_not_as_untested(absolute_paths_repo):
    """Same contract the another-tree refusal earned: a refused lane leaves its
    scopes `no-lane`, never a grade assembled out of a path-spelling mistake."""
    summary = json.loads(run_cli(absolute_paths_repo, "coverage", "--json").stdout)

    assert summary["lane_failures"]["py"]
    assert summary["no_lane"] > 0
