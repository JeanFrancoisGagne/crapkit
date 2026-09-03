"""End-to-end: a lane whose positional is pytest's own `testpaths` loads.

The reported repo: `python -m pytest tests -q --cov=app` beside a pyproject
naming `testpaths = ["tests"]`. On 0.4.15 doctor, coverage, digest and ratchet
seed all exited 3 with `positional argument 'tests' narrows a full-suite
coverage run`, from a command that collects the whole suite.

The CLI reaches the guard through `cli/_shared.py:_load_repo_config`, which
hands the loader the root it found (`load_config_text(text, root=root)`); the
loader then reads pytest's own configuration where the lane runs.
"""
import subprocess
from pathlib import Path

import pytest

from conftest import run_cli

MOD = "def g(x):\n    return x or 0\n"
TEST = "from pylib.mod import g\n\n\ndef test_g():\n    assert g(0) == 0\n"
PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
TOML = """[crapkit]
target = 6

[[scope]]
name = "py"
paths = ["pylib"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -m pytest tests -q -p no:cacheprovider --cov=pylib --cov-branch --cov-report=json:.crapkit/cov/py.json"
artifact = ".crapkit/cov/py.json"
parser = "coveragepy"
scopes = ["py"]
"""

NARROWS = "positional argument 'tests' narrows a full-suite coverage run"


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", "commit", "-q", "-m", message],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "app"
    (repo / "pylib").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pylib" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pylib" / "mod.py").write_text(MOD, encoding="utf-8")
    (repo / "tests" / "test_mod.py").write_text(TEST, encoding="utf-8")
    (repo / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\n__pycache__/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _commit(repo, "init")
    return repo


def test_the_positional_that_names_the_configured_testpaths_passes_every_command(repo: Path):
    doctor = run_cli(repo, "doctor")
    inventory = run_cli(repo, "inventory", "--json")
    coverage = run_cli(repo, "coverage", "--json", timeout=300)

    assert NARROWS not in doctor.stderr, doctor.stderr
    assert NARROWS not in inventory.stderr, inventory.stderr
    assert inventory.returncode == 0, inventory.stderr
    assert coverage.returncode == 0, coverage.stderr
    assert '"measured": 1' in coverage.stdout, "the lane ran and measured the one function"
    assert '"no_lane": 0' in coverage.stdout, coverage.stdout


def test_the_same_positional_is_refused_when_no_testpaths_names_it(repo: Path):
    """The control: take the pyproject away and the guard reads the command
    the way it always did."""
    (repo / "pyproject.toml").unlink()

    res = run_cli(repo, "doctor")

    assert res.returncode == 3, res.stderr
    assert NARROWS in res.stderr, res.stderr
