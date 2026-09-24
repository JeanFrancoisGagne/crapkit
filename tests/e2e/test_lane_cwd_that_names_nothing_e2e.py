r"""A lane whose cwd names no directory fails as a lane, with a line naming it.

Popen raised FileNotFoundError on POSIX and NotADirectoryError (WinError 267)
on Windows for a `cwd` that is not a directory. The lane layer reads a failed
start as that lane's failure only when it is crapkit's own error, so `crapkit
coverage` died with a Python traceback and exit 1, a code outside the exit
table. A config committed from Windows with `cwd = 'backend\'` reached it on
Linux; any missing cwd reached it on both OSes. doctor already FAILed the lane
by name; coverage now fails it the same way, and exits 5 when no lane is left.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner()

# A coverage.py report for backend/pkg/mod.py, keyed from backend/ as a suite
# run there keys it, written where the lane's artifact says.
_GEN = (
    "import json, os, sys\n"
    "report = {'meta': {'branch_coverage': True}, 'files': {'pkg/mod.py': {'functions': {\n"
    "    'f': {'start_line': 1, 'executed_lines': [1, 2, 3], 'missing_lines': [4],\n"
    "          'summary': {'covered_lines': 3, 'num_statements': 4,\n"
    "                      'num_branches': 2, 'covered_branches': 1}}}}}}\n"
    "os.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True)\n"
    "open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(report))\n"
)


def _repo(tmp_path: Path, cwd: str) -> Path:
    repo = tmp_path / "repo"
    files = {
        "backend/pkg/__init__.py": "",
        "backend/pkg/mod.py": "def f(x):\n    if x:\n        return 1\n    return 2\n",
        "gen_cov.py": _GEN,
        ".gitignore": ".crapkit/\n",
        "crapkit.toml": (
            "[[scope]]\nname = 'backend'\npaths = ['backend']\nlanguages = ['python']\n\n"
            "[exclude]\nglobs = ['gen_cov.py']\n\n"
            "[[lane]]\nname = 'py'\nparser = 'coveragepy'\nscopes = ['backend']\n"
            f"cwd = '{cwd}'\npath_prefix = 'backend'\n"
            "artifact = '.crapkit/cov/py.json'\n"
            f"command = 'python {os.path.join('..', 'gen_cov.py')} "
            f"{os.path.join('..', '.crapkit', 'cov', 'py.json')}'\n"),
    }
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body, encoding="utf-8")
    git_init_repo(repo)
    git_commit_all(repo, "sources")
    return repo


@pytest.mark.parametrize("cwd", ["nope", "backend/missing", "backend/pkg/mod.py"])
def test_a_lane_cwd_that_names_no_directory_fails_the_lane_by_name(tmp_path, cwd):
    repo = _repo(tmp_path, cwd)

    res = run_cli(repo, "coverage")

    assert res.returncode == 5, res.stdout + res.stderr
    assert "Traceback" not in res.stderr, res.stderr
    (line,) = [ln for ln in res.stderr.splitlines() if "FAILED" in ln]
    where = str(repo / cwd)
    assert line == (f"crapkit: lane 'py' FAILED: cwd {where} is not a directory, "
                    "so the command never ran"), line
    assert "every lane failed (1 of 1)" in res.stderr


def test_doctor_fails_the_same_lane_before_it_runs(tmp_path):
    repo = _repo(tmp_path, "nope")

    res = run_cli(repo, "doctor")

    assert res.returncode == 1, res.stdout
    assert "FAIL lane 'py': cwd 'nope' does not exist" in res.stdout


def test_the_json_run_names_the_lane_error_too(tmp_path):
    """An agent reading `coverage --json` gets the same sentence in the lane's
    error, not a traceback on stderr and an empty stdout."""
    repo = _repo(tmp_path, "nope")

    res = run_cli(repo, "coverage", "--json")

    assert res.returncode == 5, res.stdout + res.stderr
    assert "Traceback" not in res.stderr
    assert f"cwd {repo / 'nope'} is not a directory" in res.stderr


@pytest.mark.parametrize("cwd", ["backend", "backend/", "./backend", "backend\\", ".\\backend",
                                 "backend/../backend"])
def test_a_lane_cwd_that_names_the_directory_in_any_spelling_runs(tmp_path, cwd):
    """The spellings a config committed from either OS carries. On Linux
    `backend\\` and `.\\backend` were the traceback; they name backend/ now."""
    repo = _repo(tmp_path, cwd)

    res = run_cli(repo, "coverage", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert (payload["measured"], payload["untested"], payload["lane_failures"]) == (1, 0, {}), payload
