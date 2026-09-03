"""End-to-end: a lane whose positional is pytest's own `testpaths` loads.

The reported repo: `python -m pytest tests -q --cov=app` beside a pyproject
naming `testpaths = ["tests"]`. On 0.4.15 doctor, coverage, digest and ratchet
seed all exited 3 with `positional argument 'tests' narrows a full-suite
coverage run`, from a command that collects the whole suite.

The CLI reaches the guard through `cli/_shared.py:_load_repo_config`, which
has to hand the loader the root it found: `load_config_text(text, root=root)`.
That file belongs to another slice, so the test that needs the line is a
strict expected failure until it lands; it then XPASSes, and the marker comes
off in the same change.
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


@pytest.mark.xfail(strict=True, reason=(
    "cli/_shared.py:_load_repo_config must call load_config_text(text, root=root); "
    "that file is the rows slice's. Drop this marker with that one-line change."))
def test_the_positional_that_names_the_configured_testpaths_passes_every_command(repo: Path):
    doctor = run_cli(repo, "doctor")
    inventory = run_cli(repo, "inventory", "--json")

    assert NARROWS not in doctor.stderr, doctor.stderr
    assert NARROWS not in inventory.stderr, inventory.stderr
    assert inventory.returncode == 0, inventory.stderr


def test_the_same_positional_is_refused_when_no_testpaths_names_it(repo: Path):
    """The control: take the pyproject away and the guard reads the command
    the way it always did."""
    (repo / "pyproject.toml").unlink()

    res = run_cli(repo, "doctor")

    assert res.returncode == 3, res.stderr
    assert NARROWS in res.stderr, res.stderr
