"""The dev extra is the entire install contract for CI.

`.github/workflows/ci.yml` installs with `pip install -e ".[dev]"` and nothing
else, so a pytest plugin the committed fixture lanes need has to be declared
there or every test job dies before it reaches an assertion. That is exactly
what happened on run 33225190806: `tests/fixtures/mini_repo` declares a lane
that shells out to `pytest pylib -n 2 ...`, pytest-xdist was a manual side
install the docs asked for and CI never did, and all six test jobs (three
Python versions on ubuntu-latest and windows-latest alike) failed with
`unrecognized arguments: -n`.

The lane resolves `python` from PATH rather than from `sys.executable`, so a
developer whose PATH python already carries xdist sees the suite pass from a
venv that lacks it. CI has no such second interpreter, which is why the break
showed up there first.
"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"

# The pytest flags each plugin owns. A fixture lane that spells one of these
# needs that package present, or pytest rejects the argument and the lane
# writes no artifact.
PLUGIN_FLAGS = {"pytest-xdist": ("-n",), "pytest-cov": ("--cov",)}


def _dev_extra() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]["dev"]


def _distribution(requirement: str) -> str:
    """The package name in a requirement string, without version or extras."""
    return re.split(r"[<>=!~\[ ]", requirement, maxsplit=1)[0].strip()


def _fixture_pytest_commands() -> list[str]:
    commands = []
    for config in sorted((ROOT / "tests" / "fixtures").rglob("crapkit.toml")):
        data = tomllib.loads(config.read_text(encoding="utf-8"))
        commands += [lane.get("command", "") for lane in data.get("lane", [])]
    return [command for command in commands if " -m pytest " in command]


def _needs(command: str, flag: str) -> bool:
    return any(token.startswith(flag) for token in command.split())


def _plugins_the_fixture_lanes_need() -> set[str]:
    commands = _fixture_pytest_commands()
    return {dist for dist, flags in PLUGIN_FLAGS.items()
            if any(_needs(command, flag) for command in commands for flag in flags)}


def test_dev_extra_ships_every_pytest_plugin_the_fixture_lanes_need():
    declared = {_distribution(req) for req in _dev_extra()}
    needed = _plugins_the_fixture_lanes_need()

    assert needed, "no committed fixture lane runs pytest; this contract lost its subject"
    assert needed <= declared, (
        f"fixture lanes need {sorted(needed - declared)} and CI installs only "
        f'`pip install -e ".[dev]"`, so the lane dies on an unrecognized flag')


def _ci_lines() -> list[str]:
    return CI.read_text(encoding="utf-8").splitlines()


def test_ci_installs_the_dev_extra_and_quotes_it():
    installs = [line for line in _ci_lines() if "pip install -e" in line]

    assert installs, "ci.yml stopped installing crapkit"
    for line in installs:
        assert '".[dev]"' in line, (
            f"{line.strip()!r} must install the dev extra, quoted: zsh globs the "
            f"bare form and pip never sees the extra")


def test_ci_does_not_swallow_the_crapkit_gate_exit_code():
    gate = [line for line in _ci_lines() if "hook-precommit" in line]

    assert gate, "ci.yml stopped running the gate"
    for line in gate:
        assert "|| true" not in line, (
            f"{line.strip()!r} swallows the gate verdict, hiding a crash as "
            f"readily as a violation; the tree passes the gate today")
