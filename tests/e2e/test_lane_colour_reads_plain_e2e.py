"""Every reader of a failed lane gets plain text, whatever colour pytest wrote.

A lane runs with crapkit's environment, so the variables a CI job sets to
colour its log reach pytest: FORCE_COLOR (any value, "0" included), PY_COLORS=1,
and on Python 3.14 PYTHON_COLORS=1 for pytest's argparse usage error. TERM=dumb
does not stop FORCE_COLOR. Before 0.8.1 the colour codes reached every reader
of the refusal, and a code in front of `E   ` hid the cause from the scan that
hoists it.

One repo, four lanes, real pytest on the interpreter running this suite (the
CI legs put it on 3.11 to 3.14, Windows and Ubuntu):

  ok      passes, so `coverage --json` prints a run summary
  broken  15 test modules that fail at import; no coverage JSON
  nocov   pytest with pytest-cov blocked (`-p no:pytest_cov`), which refuses
          --cov exactly as an interpreter without the package does
  xdist   `-n 2` with one module failing at import; junit holds the coloured
          collection error as `#x1B` text

Each row is one colour environment; each cell a reader of the refusal: the
stderr line, `--json` lane_failures, the hoisted cause, the pytest-cov hint,
the junit collection refusal, the pull-request comment the Action builds, and
the crapkit-base.reason that action.yml's base step writes from `head -n 1` of
crapkit's stderr, run as written under bash.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import hang_guard
import pytest

from conftest import child_env, cli_runner, git_commit_all, git_init_repo
from repo_templates import copy_of, template

run_cli = cli_runner(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "tools" / "action" / "comment.py"
ACTION = ROOT / "action.yml"
ESC = "\x1b"
HINT = "the --cov flags come from the pytest-cov package"
CAUSE = "last output: E   ModuleNotFoundError: No module named 'brokenpkg.missing_"
JUNIT = "collection error: collection failure ImportError while importing test module"

# Every variable that moves a child's colour. Each row sets its own and drops
# the rest, so a CI job's own FORCE_COLOR cannot decide a cell.
_KNOBS = ("FORCE_COLOR", "NO_COLOR", "PY_COLORS", "PYTHON_COLORS", "TERM", "CLICOLOR_FORCE",
          "PYTEST_ADDOPTS")
COLOUR_ENVS = {
    "none": {},
    "FORCE_COLOR=1": {"FORCE_COLOR": "1"},
    "FORCE_COLOR=0": {"FORCE_COLOR": "0"},
    "PY_COLORS=1": {"PY_COLORS": "1"},
    "PYTHON_COLORS=1": {"PYTHON_COLORS": "1"},
    "PY_COLORS=0 FORCE_COLOR=1": {"PY_COLORS": "0", "FORCE_COLOR": "1"},
    "NO_COLOR=1 FORCE_COLOR=1": {"NO_COLOR": "1", "FORCE_COLOR": "1"},
    "TERM=dumb FORCE_COLOR=1": {"TERM": "dumb", "FORCE_COLOR": "1"},
}
# The rows where pytest itself colours the broken lane's log; the file on disk
# keeps that colour, which is what makes the other cells of the row a test.
_PYTEST_COLOURS = {"FORCE_COLOR=1", "FORCE_COLOR=0", "PY_COLORS=1", "TERM=dumb FORCE_COLOR=1"}

_FUNC = "def twice(x):\n    if x:\n        return 2 * x\n    return 0\n"
_PASSING = "from {pkg}.calc import twice\n\n\ndef test_{name}():\n    assert twice(2) == 4\n"
_BROKEN = "import {pkg}.missing_{n:02}  # noqa: F401\n\n\ndef test_x():\n    assert True\n"
_LANE = """
[[lane]]
name = "{name}"
command = "python -m pytest tests_{name} -p no:cacheprovider -p no:randomly{flags} --cov={pkg} --cov-branch --cov-report=json:.crapkit/cov/{name}.json --junitxml=.crapkit/cov/{name}.xml"
artifact = ".crapkit/cov/{name}.json"
results_artifact = ".crapkit/cov/{name}.xml"
parser = "coveragepy"
scopes = ["{pkg}"]
full_suite = false
env = {{ COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = "" }}
"""
_LANES = (("ok", ""), ("broken", ""), ("nocov", " -p no:pytest_cov"), ("xdist", " -n 2"))


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _tests(name: str, pkg: str) -> dict[str, str]:
    if name == "broken":
        return {f"tests_broken/test_broken_{n:02}.py": _BROKEN.format(pkg=pkg, n=n) for n in range(15)}
    files = {f"tests_{name}/test_{name}_{n}.py": _PASSING.format(pkg=pkg, name=f"{name}_{n}") for n in (1, 2)}
    if name == "xdist":
        files["tests_xdist/test_xdist_3.py"] = _BROKEN.format(pkg=pkg, n=3)
    return files


def _config(lanes) -> str:
    config = ["[crapkit]\ntarget = 6\n"]
    for name, flags in lanes:
        pkg = f"{name}pkg"
        config.append(f'\n[[scope]]\nname = "{pkg}"\npaths = ["{pkg}"]\nlanguages = ["python"]\n')
        config.append(_LANE.format(name=name, pkg=pkg, flags=flags))
    config.append('\n[exclude]\nglobs = ["tests_*/**"]\n')
    return "".join(config)


def _build(repo: Path) -> None:
    """Two commits. The first declares the broken lane alone and is the fork
    point the Action's base step scores, which keeps that second run to one
    lane; the second declares all four."""
    for name, _ in _LANES:
        pkg = f"{name}pkg"
        _write(repo, f"{pkg}/__init__.py", "")
        _write(repo, f"{pkg}/calc.py", _FUNC)
        for rel, text in _tests(name, pkg).items():
            _write(repo, rel, text)
    _write(repo, "pytest.ini", "[pytest]\n")
    _write(repo, ".gitignore", ".crapkit/\n")
    _write(repo, "crapkit.toml", _config([lane for lane in _LANES if lane[0] == "broken"]))
    git_init_repo(repo)
    git_commit_all(repo, "the broken lane")
    _write(repo, "crapkit.toml", _config(_LANES))
    git_commit_all(repo, "four lanes")


def _colour_env(row: str) -> dict:
    return {**dict.fromkeys(_KNOBS), **COLOUR_ENVS[row]}


def _unplain(text: str) -> str:
    """Where the first escape code sits, or "" when there is none."""
    at = min((i for i in (text.find(ESC), text.find("#x1B")) if i >= 0), default=-1)
    return "" if at < 0 else repr(text[max(0, at - 60):at + 40])


@lru_cache(maxsize=None)
def _builder():
    spec = importlib.util.spec_from_file_location("crapkit_action_comment_e2e", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _comment(tmp_path: Path, coverage_json: str, exit_code: int, reason: str) -> str:
    """The comment for this failed coverage, and the verdict line a pull request
    whose base run failed the same way gets."""
    (tmp_path / "cov.json").write_text(coverage_json, encoding="utf-8")
    (tmp_path / "verify.json").write_text(json.dumps({"ok": True, "run_id": 2, "baseline_run": 1}),
                                          encoding="utf-8")
    (tmp_path / "base.sha").write_text("", encoding="utf-8")
    (tmp_path / "base.reason").write_text(reason, encoding="utf-8")
    _builder().main(["--coverage", str(tmp_path / "cov.json"), "--coverage-exit", str(exit_code),
                     "--out", str(tmp_path / "failed.md")])
    _builder().main(["--verify", str(tmp_path / "verify.json"), "--base-sha", str(tmp_path / "base.sha"),
                     "--base-reason", str(tmp_path / "base.reason"), "--out", str(tmp_path / "base.md")])
    return "\n".join((tmp_path / name).read_text(encoding="utf-8") for name in ("failed.md", "base.md"))


def _unusable(bash: str | None) -> bool:
    """No bash, or the WSL launcher under System32, which cannot see this
    test's files."""
    return bash is None or "system32" in bash.lower()


def _git_bash() -> str | None:
    """bin/bash.exe beside the git on PATH: Git for Windows ships both."""
    git = shutil.which("git")
    if git is None:
        return None
    candidate = Path(git).parent.parent / "bin" / "bash.exe"
    return str(candidate) if candidate.is_file() else None


def _bash() -> str:
    """The bash a runner's `shell: bash` step runs under: Git Bash on Windows."""
    bash = shutil.which("bash")
    if os.name == "nt" and _unusable(bash):
        bash = _git_bash()
    if _unusable(bash):
        pytest.skip("no bash to run action.yml's step under")
    return bash


def _base_step() -> str:
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    return next(step["run"] for step in steps if step.get("name") == "score the base commit")


def _base_reason(tmp_path: Path, repo: Path, row: str) -> str:
    """action.yml's base step as written, with `crapkit` on PATH meaning this
    suite's interpreter, at a fork point whose broken lane fails as the
    head's does."""
    shim = tmp_path / "bin"
    shim.mkdir()
    python = Path(sys.executable).as_posix()
    (shim / "crapkit").write_text(f'#!/usr/bin/env bash\nexec "{python}" -m crapkit "$@"\n',
                                  encoding="utf-8", newline="\n")
    (shim / "crapkit").chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    fork = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()
    env = child_env({**_colour_env(row), "CRAPKIT_STATE": state.as_posix(), "BASE_SHA": fork})
    env["PATH"] = os.pathsep.join([str(shim), env["PATH"]])
    script = tmp_path / "base-step.sh"
    script.write_text(_base_step(), encoding="utf-8", newline="\n")
    step = hang_guard.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                          cwd=repo, env=env)
    assert step.returncode == 0, step.stdout + step.stderr
    return (state / "crapkit-base.reason").read_text(encoding="utf-8", errors="replace")


def _cells(result, reason: str, comment: str) -> dict[str, str]:
    """Each reader's text, and what it must hold beyond being plain."""
    failures = json.loads(result.stdout).get("lane_failures") or {}
    return {
        "stderr": (result.stderr, "lane 'broken' FAILED"),
        "json broken": (failures.get("broken", ""), "lane 'broken'"),
        "cause hoisted": (failures.get("broken", ""), CAUSE),
        "pytest-cov hint": (failures.get("nocov", ""), HINT),
        "junit refusal": (failures.get("xdist", ""), JUNIT),
        "PR comment": (comment, "lane 'broken' failed: lane 'broken' produced no artifact"),
        "crapkit-base.reason": (reason, "E   ModuleNotFoundError"),
    }


def _fault(text: str, needle: str) -> str:
    """What is wrong with one cell, or "" when it is plain and holds `needle`."""
    return _unplain(text) or ("" if needle in text else f"missing {needle!r}")


def _log_colour(repo: Path) -> bool:
    return ESC in (repo / ".crapkit" / "lane-broken.log").read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("row", list(COLOUR_ENVS), ids=list(COLOUR_ENVS))
def test_every_reader_of_a_failed_lane_gets_plain_text(tmp_path, row):
    repo = copy_of(template(tmp_path, "colour-lanes", _build), tmp_path / "repo")
    result = run_cli(repo, "coverage", "--json", env_extra=_colour_env(row))
    assert result.returncode == 5, result.stdout + result.stderr
    reason = _base_reason(tmp_path, repo, row)
    comment = _comment(tmp_path, result.stdout, result.returncode, reason)

    cells = _cells(result, reason, comment)
    bad = [f"{cell}: {_fault(*cells[cell])}" for cell in cells if _fault(*cells[cell])]

    assert not bad, f"{row} (lane on Python {sys.version.split()[0]}):\n" + "\n".join(bad)
    assert _log_colour(repo) == (row in _PYTEST_COLOURS), "the lane log keeps the colour pytest wrote"


# The settings that leave pytest plain, or move where its tail breaks. One lane
# is enough to show the cause still leads: pytest's separators follow COLUMNS,
# and a wider one spends more of the 500-character tail.
QUIET_ENVS = {
    "TERM=dumb": {"TERM": "dumb"},
    "NO_COLOR=1": {"NO_COLOR": "1"},
    "COLUMNS=40": {"COLUMNS": "40"},
    "COLUMNS=250": {"COLUMNS": "250"},
    "COLUMNS=250 FORCE_COLOR=1": {"COLUMNS": "250", "FORCE_COLOR": "1"},
}


@pytest.mark.parametrize("row", list(QUIET_ENVS), ids=list(QUIET_ENVS))
def test_the_broken_lane_names_its_cause_at_any_width(tmp_path, row):
    repo = copy_of(template(tmp_path, "colour-lanes", _build), tmp_path / "repo")
    extra = {**dict.fromkeys((*_KNOBS, "COLUMNS")), **QUIET_ENVS[row]}
    result = run_cli(repo, "coverage", "--lane", "broken", env_extra=extra)

    assert result.returncode == 5, result.stdout + result.stderr
    assert _fault(result.stderr, CAUSE) == "", result.stderr
