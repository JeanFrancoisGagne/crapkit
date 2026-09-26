"""A healthy repo prints no escape code under any colour variable.

crapkit writes no colour of its own, and on a repo where every lane passes no
child text reaches its output: the lane's coloured pytest output stays in the
lane log. These rows run the commands a person, a script or an agent reads
(coverage, the read commands, the gate, verify, the hook, the report page)
with each variable that turns colour on for a pipe, and hold every stdout,
stderr and the report page to zero ESC bytes and to the exit codes of the run
with none. PYTHON_COLORS decides colour only from Python 3.13 on, and 3.14's
argparse reads FORCE_COLOR too; the 3.14 CI legs are where those rows bite.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import cli_runner, git, git_commit_all, git_init_repo
from repo_templates import copy_of, template

run_cli = cli_runner(encoding="utf-8", errors="replace")

ESC = "\x1b"
_KNOBS = ("FORCE_COLOR", "NO_COLOR", "PY_COLORS", "PYTHON_COLORS", "TERM", "CLICOLOR_FORCE", "PYTEST_ADDOPTS")
ENVS = {
    "none": {},
    "FORCE_COLOR=1": {"FORCE_COLOR": "1"},
    "PY_COLORS=1": {"PY_COLORS": "1"},
    "PYTHON_COLORS=1": {"PYTHON_COLORS": "1"},
    "CLICOLOR_FORCE=1": {"CLICOLOR_FORCE": "1"},
}
TOML = """[crapkit]
target = 6

[[scope]]
name = "pkg"
paths = ["pkg"]
languages = ["python"]

[exclude]
globs = ["tests/**"]

[[lane]]
name = "py"
command = "python -m pytest tests -p no:cacheprovider -p no:randomly --cov=pkg --cov-branch --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/py.xml"
artifact = ".crapkit/cov/py.json"
results_artifact = ".crapkit/cov/py.xml"
parser = "coveragepy"
scopes = ["pkg"]
full_suite = false
env = { COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = "" }
"""
CALC = '''def grade(score):
    if score > 90:
        return "A"
    elif score > 80:
        return "B"
    elif score > 70:
        return "C"
    return "F"


def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
'''
TESTS = """from pkg.calc import clamp, grade


def test_grade():
    assert grade(95) == "A"


def test_clamp():
    assert clamp(5, 0, 10) == 5
"""
# Every command after `coverage` reads the run it wrote; `rescore --gate` and
# `hook-precommit` judge the staged edit the row makes first.
COMMANDS = (
    ("worklist",), ("worklist", "--json"), ("next-item",), ("brief", "pkg/calc.py", "grade", "--json"),
    ("explain", "pkg/calc.py", "grade", "--history"), ("runs",), ("trend",), ("digest",),
    ("duplication",), ("coupling",), ("doctor",), ("ratchet", "seed"),
    ("verify", "--reuse-artifacts"), ("report", "--out", "report.html"),
    ("rescore", "pkg/calc.py", "--gate"), ("hook-precommit",),
)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _build(repo: Path) -> None:
    for rel, text in {"crapkit.toml": TOML, "pkg/__init__.py": "", "pkg/calc.py": CALC,
                      "tests/test_calc.py": TESTS, "pytest.ini": "[pytest]\n",
                      ".gitignore": ".crapkit/\nreport.html\n"}.items():
        _write(repo, rel, text)
    git_init_repo(repo)
    git_commit_all(repo, "init")
    _write(repo, "pkg/calc.py", CALC.replace('return "F"', 'return "E"'))
    git_commit_all(repo, "grade E")


def _session(repo: Path, row: str) -> dict:
    """Every command's result, in order, under the row's variables."""
    extra = {**dict.fromkeys(_KNOBS), **ENVS[row]}
    results = {"coverage": run_cli(repo, "coverage", env_extra=extra),
               "coverage --json": run_cli(repo, "coverage", "--json", env_extra=extra)}
    _write(repo, "pkg/calc.py", CALC.replace("return x", "return x + 0"))
    git(repo, "add", "pkg/calc.py")
    for argv in COMMANDS:
        results[" ".join(argv)] = run_cli(repo, *argv, env_extra=extra)
    return results


@pytest.fixture(scope="module")
def control(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("control")
    results = _session(copy_of(template(tmp_path, "healthy", _build), tmp_path / "repo"), "none")
    assert results["coverage"].returncode == 0, results["coverage"].stderr
    return {name: result.returncode for name, result in results.items()}


@pytest.mark.parametrize("row", list(ENVS), ids=list(ENVS))
def test_a_healthy_repo_prints_no_escape_code(tmp_path, control, row):
    repo = copy_of(template(tmp_path, "healthy", _build), tmp_path / "repo")
    results = _session(repo, row)

    escaped = [name for name, result in results.items() if ESC in result.stdout + result.stderr]
    assert not escaped, f"{row}: escape codes in {escaped}"
    assert ESC not in (repo / "report.html").read_text(encoding="utf-8"), "the report page"
    assert {name: result.returncode for name, result in results.items()} == control
