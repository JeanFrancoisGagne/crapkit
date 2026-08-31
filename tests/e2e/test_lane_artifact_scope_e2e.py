"""End-to-end: an artifact about another checkout never becomes a grade.

The near-miss behind this. Two git worktrees of one repo, and a shell holding
the OTHER worktree's venv, whose editable install points at that checkout's
sources. The lane runs here, coverage.py measures there, and because the file
lives outside this tree it is reported by absolute path. Had the two checkouts'
APIs matched, the suite would have passed, the join would have found nothing,
and `coverage` would have printed a confident "N untested … grade F" that was
entirely an artifact of the wrong venv.

Real git, real CLI. The istanbul lane is the hermetic one, so it plays the part.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# make_cov.py, but writing about a sibling checkout instead of this one — the
# shape coverage.py and istanbul both produce for a file outside the tree.
ELSEWHERE = (
    "import json, os\n"
    "root = os.path.dirname(os.getcwd())\n"
    "app = os.path.join(root, 'other-checkout', 'src', 'app.ts').replace(os.sep, '/')\n"
    "artifact = {app: {'path': app,\n"
    "    'fnMap': {'0': {'name': 'dispatch', 'decl': {'start': {'line': 1}},\n"
    "                    'loc': {'start': {'line': 1}, 'end': {'line': 10}}}},\n"
    "    'f': {'0': 3},\n"
    "    'branchMap': {'0': {'loc': {'start': {'line': 2}},\n"
    "                        'locations': [{'start': {'line': 2}}, {'start': {'line': 3}}]}},\n"
    "    'b': {'0': [1, 1]}}}\n"
    "os.makedirs(os.path.join(os.getcwd(), 'coverage'), exist_ok=True)\n"
    "open(os.path.join('coverage', 'coverage-final.json'), 'w', encoding='utf-8')"
    ".write(json.dumps(artifact))\n"
)


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo,
                          capture_output=True, text=True, timeout=180, env=dict(os.environ))


@pytest.fixture()
def wrong_tree_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    (repo / "make_cov.py").write_text(ELSEWHERE, encoding="utf-8")
    (repo / ".gitignore").write_text(
        ".crapkit/\ncoverage/\ncoverage-py.json\njunit.xml\n__pycache__/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_a_lane_measuring_another_checkout_fails_instead_of_grading_it(wrong_tree_repo: Path):
    res = run_cli(wrong_tree_repo, "coverage", "--json")

    assert res.returncode == 5, res.stderr
    assert "lane 'unit' FAILED" in res.stderr
    assert "none of them under the paths its scopes declare" in res.stderr
    assert "other-checkout/src/app.ts" in res.stderr, "the paths it did measure are quoted"


def test_the_scope_that_lane_claimed_reads_as_a_tooling_gap_not_as_untested(
        wrong_tree_repo: Path):
    """The distinction the whole finding is about. `untested` says nobody wrote
    a test; `no-lane` says nothing measured this. A refused lane must produce
    the second, and the second is what keeps the grade honest."""
    summary = json.loads(run_cli(wrong_tree_repo, "coverage", "--json").stdout)

    assert summary["no_lane"] > 0
    assert summary["untested"] == 0
    assert summary["lane_failures"]["unit"]
