"""End-to-end: --reuse-unchanged skips lanes whose scopes didn't move, and
--reuse-artifacts warns when the artifact went stale. Real git, real CLI.

The unit lane's command is swapped for a counting wrapper so each test can
assert HOW MANY times the lane actually executed, not just that it succeeded.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args],
                          cwd=repo, capture_output=True, text=True, timeout=120, env=dict(os.environ))

COUNTER = (
    "import pathlib, subprocess, sys\n"
    "with open('runs.txt', 'a', encoding='utf-8') as f:\n"
    "    f.write('run\\n')\n"
    "sys.exit(subprocess.run([sys.executable, 'make_cov.py']).returncode)\n"
)


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", message], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def counting_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    (repo / "run_counted.py").write_text(COUNTER, encoding="utf-8")
    # like any real repo: generated output is ignored, so a later `git add -A`
    # commits source changes only, not run 1's coverage droppings
    (repo / ".gitignore").write_text(
        ".crapkit/\ncoverage/\ncoverage-py.json\njunit.xml\nruns.txt\n__pycache__/\n",
        encoding="utf-8")
    toml = (repo / "crapkit.toml").read_text(encoding="utf-8")
    toml = toml.replace('command = "python make_cov.py"', 'command = "python run_counted.py"')
    (repo / "crapkit.toml").write_text(toml, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _commit(repo, "init")
    return repo


def _run_count(repo: Path) -> int:
    marker = repo / "runs.txt"
    if not marker.is_file():
        return 0
    return len(marker.read_text(encoding="utf-8").splitlines())


def test_reuse_unchanged_skips_lanes_when_nothing_moved(counting_repo: Path):
    first = run_cli(counting_repo, "coverage", "--json")
    assert first.returncode == 0, first.stderr
    assert _run_count(counting_repo) == 1

    second = run_cli(counting_repo, "coverage", "--reuse-unchanged", "--json")
    assert second.returncode == 0, second.stderr
    assert _run_count(counting_repo) == 1, "unchanged scopes must not rerun the lane"
    assert "reusing without rerun" in second.stderr
    assert json.loads(second.stdout)["functions"] == json.loads(first.stdout)["functions"]


def test_reuse_unchanged_reruns_a_lane_whose_scope_moved(counting_repo: Path):
    assert run_cli(counting_repo, "coverage", "--json").returncode == 0
    app = counting_repo / "src" / "app.ts"
    app.write_text(app.read_text(encoding="utf-8") + "\n// touched\n", encoding="utf-8")
    _commit(counting_repo, "touch src")

    res = run_cli(counting_repo, "coverage", "--reuse-unchanged", "--json")
    assert res.returncode == 0, res.stderr
    assert _run_count(counting_repo) == 2, "a changed scope must rerun its lane"
    assert "lane 'py': artifact still matches" in res.stderr, "the untouched lane is still reused"


def test_reuse_artifacts_warns_when_the_artifact_went_stale(counting_repo: Path):
    assert run_cli(counting_repo, "coverage", "--json").returncode == 0
    app = counting_repo / "src" / "app.ts"
    app.write_text(app.read_text(encoding="utf-8") + "\n// drifted\n", encoding="utf-8")

    res = run_cli(counting_repo, "coverage", "--reuse-artifacts", "--json")
    assert res.returncode == 0, res.stderr
    assert _run_count(counting_repo) == 1, "--reuse-artifacts never reruns"
    assert "stale" in res.stderr, "reuse over a moved scope must say the coverage is stale"
