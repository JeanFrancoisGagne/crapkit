r"""The python a committed crapkit.toml names, read on the OS that runs it.

`crapkit init` wrote the launcher of the OS it ran on: `.venv\Scripts\python.exe`
on Windows, `.venv/bin/python` elsewhere, and a bare `python` or `python3`
where no venv carried pytest. crapkit.toml is committed, so a Linux checkout
with its own `.venv` read the Windows author's lane, sh stripped the
backslashes, and every lane failed with `.venvScriptspython.exe: not found`
(exit 5). The Linux author's `.venv/bin/python` failed the same way on Windows,
and a bare `python` failed on an Ubuntu without python-is-python3.

init now writes a launcher token, `{python}` or `{python:DIR}`, and the config
loader expands it for the OS reading the file, before anything else reads the
command. Every reader (lanes, retests, doctor's probes, scoped_tests, mutate)
sees the expanded command, so none of them has to know the token exists.
"""
from __future__ import annotations

import os
import tomllib

import pytest

from crapkit import config
from crapkit.config import load_config_text
from crapkit.errors import ConfigError
from crapkit.lane_command import (expand_launchers, first_word, is_python, pytest_python,
                                  python_token)
from crapkit.procs import prepare_template

WINDOWS = os.name == "nt"


@pytest.mark.parametrize("token, windows, expanded", [
    ("{python}", True, "python"),
    ("{python}", False, "python3"),
    ("{python:.venv}", True, ".venv\\Scripts\\python.exe"),
    ("{python:.venv}", False, ".venv/bin/python"),
    ("{python:venv}", True, "venv\\Scripts\\python.exe"),
    ("{python:api/.venv}", True, "api\\.venv\\Scripts\\python.exe"),
    ("{python:api/.venv}", False, "api/.venv/bin/python"),
    ("{python:api\\.venv}", False, "api/.venv/bin/python"),
    ("{python:./.venv}", False, ".venv/bin/python"),
    ("{python:.\\.venv}", True, ".venv\\Scripts\\python.exe"),
])
def test_the_token_expands_to_the_launcher_of_the_os_reading_the_file(token, windows, expanded):
    assert expand_launchers(f"{token} -m pytest --cov", windows=windows) == \
        f"{expanded} -m pytest --cov"


def test_python_token_names_the_venv_it_was_given():
    assert python_token() == "{python}"
    assert python_token(".venv") == "{python:.venv}"
    assert python_token("api/.venv") == "{python:api/.venv}"


@pytest.mark.parametrize("command", [
    "python -m pytest", "{pythonx} -m pytest", "{python:} -m pytest", "{PYTHON} -m pytest",
    "echo {python", "uv run python -m pytest",
])
def test_text_that_is_not_the_token_is_left_as_written(command):
    """A command without the token, or with something that only resembles it,
    reaches the shell exactly as the file holds it."""
    for windows in (True, False):
        assert expand_launchers(command, windows=windows) == command


def test_every_token_on_the_line_expands():
    """A chained lane names its python in each step."""
    command = "{python:.venv} -m pytest --cov && {python:.venv} -m coverage json"

    assert expand_launchers(command, windows=False) == \
        ".venv/bin/python -m pytest --cov && .venv/bin/python -m coverage json"


@pytest.mark.parametrize("token", ["{python}", "{python:.venv}", "{python:api/.venv}"])
def test_the_token_survives_the_toml_basic_string_init_writes_it_into(token):
    """init writes each command in double quotes, where `\\` opens an escape.
    The token holds none, so the file parses and hands back the token itself."""
    assert tomllib.loads(f'command = "{token} -m pytest --cov"')["command"] == \
        f"{token} -m pytest --cov"


@pytest.mark.parametrize("token", ["{python}", "{python:.venv}", "{python:api/.venv}"])
def test_the_expansion_is_a_python_to_the_probe_that_asks_one(token):
    """doctor asks the lane's python whether pytest-cov imports, and lanes.py
    names it in the missing-plugin hint. Both read the loaded command."""
    expanded = expand_launchers(f"{token} -m pytest --cov")

    assert is_python(first_word(expanded)), expanded
    assert pytest_python(expanded) == first_word(expanded)


SCOPE = "[[scope]]\nname = 'pkg'\npaths = ['pkg']\nlanguages = ['python']\n"


def _config(**crapkit: str) -> str:
    table = "".join(f'{key} = "{value}"\n' for key, value in crapkit.items())
    return f"[crapkit]\n{table}" if table else ""


def _lane(command: str, **extra: str) -> str:
    fields = "".join(f'{key} = "{value}"\n' for key, value in extra.items())
    return (f'[[lane]]\nname = "py"\ncommand = "{command}"\nartifact = ".crapkit/cov.json"\n'
            f'parser = "coveragepy"\nscopes = ["pkg"]\n{fields}')


def test_the_loaded_config_holds_the_expansion_in_every_command():
    """Lanes, the flake retest, `crapkit test-scoped` and mutate each read one
    command out of the Config. The token is expanded there, once."""
    text = (_config(mutation_command="{python:.venv} -m pytest -q -x")
            + "[crapkit.scoped_tests]\npkg = \"{python:.venv} -m pytest {files} -q\"\n"
            + SCOPE
            + _lane("{python:.venv} -m pytest --cov", retest_command="{python:.venv} -m pytest {tests}"))
    launcher = expand_launchers("{python:.venv}")

    cfg = load_config_text(text)

    (lane,) = cfg.lanes
    assert lane.command == f"{launcher} -m pytest --cov"
    assert lane.retest_command == f"{launcher} -m pytest {{tests}}"
    assert dict(cfg.scoped_tests)["pkg"] == f"{launcher} -m pytest {{files}} -q"
    assert cfg.mutation_command == f"{launcher} -m pytest -q -x"


def test_the_loader_expands_each_command_through_lane_command(monkeypatch):
    """lane_command owns how a lane's command reads, the launcher token
    included; config asks it once per command as it builds the Config."""
    from crapkit import lane_command

    seen: list[str] = []
    monkeypatch.setattr(lane_command, "expand_launchers",
                        lambda command: seen.append(command) or command)
    text = (_config(mutation_command="{python} -m pytest -q")
            + "[crapkit.scoped_tests]\npkg = \"{python} -m pytest {files}\"\n"
            + SCOPE + _lane("{python} -m pytest --cov", retest_command="{python} -m pytest"))

    load_config_text(text)

    assert sorted(seen) == ["{python} -m pytest", "{python} -m pytest --cov",
                            "{python} -m pytest -q", "{python} -m pytest {files}"]


def test_the_files_template_still_substitutes_after_the_expansion():
    """`{files}` is test-scoped's placeholder, filled in at run time. The token
    is gone by then, and the placeholder beside it is untouched."""
    cfg = load_config_text("[crapkit.scoped_tests]\npkg = \"{python} -m pytest {files} -q\"\n"
                           + SCOPE)

    command, _ = prepare_template(dict(cfg.scoped_tests)["pkg"], {"files": ["pkg/test_a.py"]})

    assert command.startswith(f"{expand_launchers('{python}')} -m pytest ")
    assert "{files}" not in command and "{python}" not in command
    assert "pkg/test_a.py" in command or "CRAPKIT_LITERAL_0" in command


def test_the_full_suite_guard_judges_the_expanded_command():
    """The guard reads what the shell will run. A positional that narrows the
    suite is refused behind the token as it is behind a bare `python`."""
    with pytest.raises(ConfigError) as refusal:
        load_config_text(SCOPE + _lane("{python:.venv} -m pytest --cov --cov-branch pkg/unit"))

    assert "'pkg/unit' narrows a full-suite coverage run" in str(refusal.value)
    assert load_config_text(SCOPE + _lane("{python:.venv} -m pytest --cov --cov-branch")).lanes


def test_a_windows_lane_under_cmd_reads_the_expanded_launcher_as_one_word(monkeypatch):
    r"""cmd.exe needs the backslash (`.venv/Scripts/python` answers `'.venv' is
    not recognized`), and its reading of `.venv\Scripts\python.exe` is one word."""
    monkeypatch.setattr(config, "SHELL_IS_CMD", True)
    expanded = expand_launchers("{python:.venv} -m pytest --cov", windows=True)

    assert config.shell_words(expanded)[0] == ".venv\\Scripts\\python.exe"


@pytest.mark.skipif(not WINDOWS, reason="the loader expands for the OS it runs on")
def test_windows_loads_the_token_as_the_windows_launcher():
    (lane,) = load_config_text(SCOPE + _lane("{python:.venv} -m pytest --cov")).lanes
    assert lane.command.startswith(".venv\\Scripts\\python.exe -m pytest")


@pytest.mark.skipif(WINDOWS, reason="the loader expands for the OS it runs on")
def test_posix_loads_the_token_as_the_posix_launcher():
    (lane,) = load_config_text(SCOPE + _lane("{python:.venv} -m pytest --cov")).lanes
    assert lane.command.startswith(".venv/bin/python -m pytest")
