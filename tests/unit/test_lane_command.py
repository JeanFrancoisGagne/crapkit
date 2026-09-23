"""How a lane starts and how its command reads: the directory and environment its
child gets, the step that runs pytest, and the python heading that step.

lanes.py starts the lane from these and names that python in its missing
pytest-cov hint; doctor resolves words and asks that python whether pytest-cov
imports. Both read the lane through lane_command, so the rules are pinned here
once rather than through either caller's privates.
"""
import os
from pathlib import Path

import pytest

from crapkit.config import Lane
from crapkit.lane_command import (first_word, is_python, launch_spec, pytest_head,
                                  pytest_python, pytest_step)


# --- only a python running pytest can be asked about pytest_cov ---------------
#
# The probe assumed the first word is an interpreter. `coverage run -m pytest
# --cov=pylib && coverage json` starts with `coverage`, so it shelled
# `coverage -c "import pytest_cov"`, read coverage's own argument error as a
# missing package, and printed the pip note where pytest_cov imports fine.

@pytest.mark.parametrize("command, python", [
    ("python -m pytest --cov", "python"),
    ("python3 -m pytest --cov", "python3"),
    ("py -3 -m pytest --cov", "py"),
    ("/usr/bin/python3.12 -m pytest --cov", "/usr/bin/python3.12"),
    ('"C:/Program Files/Python311/python.exe" -m pytest --cov', "C:/Program Files/Python311/python.exe"),
    ("npm run build && python -m pytest --cov", "python"),
    ("cd web && python -m pytest --cov=src", "python"),
    ("set X=1 && python -m pytest --cov=src", "python"),
    ("coverage run -m pytest --cov=pylib && coverage json", None),
    ("tox -e py311 -- --cov", None),  # no pytest on the line at all
    ("npx vitest run --coverage", None),
    ("uv run python -m pytest --cov", None),
    ("pytest --cov=src", None),
])
def test_only_a_python_heading_the_pytest_step_is_named(command, python):
    assert pytest_python(command) == python


def test_the_pytest_step_is_the_segment_holding_pytest():
    assert pytest_step("npm run build && python -m pytest --cov && coverage json") == \
        ["python", "-m", "pytest", "--cov"]
    assert pytest_step("npx vitest run --coverage") == []


@pytest.mark.parametrize("command, head", [
    ("cd pkg && uv run python -m pytest --cov", "uv"),
    ("coverage run -m pytest --cov=pylib", "coverage"),
    ("npx vitest run --coverage", "npx"),
])
def test_the_head_is_the_word_in_front_of_pytest_or_else_the_first_word(command, head):
    assert pytest_head(command) == head


def test_a_quoted_interpreter_path_is_one_first_word():
    assert first_word('"C:/Program Files/py/python.exe" -m pytest') == "C:/Program Files/py/python.exe"
    assert first_word("") == ""


@pytest.mark.parametrize("word, python", [
    ("python", True), ("python3", True), ("py", True), ("python3.12", True),
    ("C:/Program Files/Python311/python.exe", True), (".venv/bin/python", True),
    ("pytest", False), ("uv", False), ("coverage", False), ("pypy", False),
])
def test_a_python_is_named_by_the_last_segment_of_the_word(word, python):
    assert is_python(word) is python


# --- where the lane's child starts, and what it sees ---------------------------
#
# lanes.py starts the lane from `root / cwd` with `{**os.environ, **lane.env}`,
# and doctor used to rebuild each half by hand: the cwd three times, the PATH
# key rule once, in a mirror of the merge. The launch spec is the one copy.

def _lane(**fields) -> Lane:
    return Lane(name="be", command="runner --cov", artifact="cov.json",
                parser="coveragepy", scopes=("be",), **fields)


def test_the_child_starts_in_the_lanes_cwd_under_the_root(tmp_path):
    assert launch_spec(tmp_path, _lane(cwd="web")).cwd == tmp_path / "web"
    assert launch_spec(tmp_path, _lane()).cwd == tmp_path


def test_the_lanes_env_is_merged_over_the_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAPKIT_SPEC_SET", "process")
    monkeypatch.setenv("CRAPKIT_SPEC_KEPT", "process")

    env = launch_spec(tmp_path, _lane(env=(("CRAPKIT_SPEC_SET", "lane"),))).child_env()

    assert (env["CRAPKIT_SPEC_SET"], env["CRAPKIT_SPEC_KEPT"]) == ("lane", "process")


def test_a_lane_that_adds_nothing_lets_the_child_inherit(tmp_path):
    assert launch_spec(tmp_path, _lane()).popen_kwargs() == {"cwd": tmp_path, "env": None}


def test_what_a_caller_adds_goes_over_the_lanes_own(tmp_path):
    """The flake retest hands its test ids to the command through the
    environment, on top of whatever the lane itself sets."""
    assert launch_spec(tmp_path, _lane(env=(("A", "lane"),))).child_env({"A": "retest"})["A"] == \
        "retest"
    assert launch_spec(tmp_path, _lane()).popen_kwargs({"B": "1"})["env"]["B"] == "1"


def test_lanes_with_one_cwd_and_env_share_one_spec(tmp_path):
    """What a probe memo keys on: two lanes whose children start the same way
    get one answer, and a different env is a different question."""
    first = launch_spec(tmp_path, _lane())
    second = launch_spec(tmp_path, Lane(name="ui", command="pnpm vitest", artifact="ui.json",
                                        parser="istanbul", scopes=("ui",)))

    assert first == second and hash(first) == hash(second)
    assert launch_spec(tmp_path, _lane(env=(("CI", "1"),))) != first


def test_only_windows_reads_a_mis_cased_key_as_the_path(tmp_path):
    """`Path` and `PATH` are one name to Windows and two to POSIX. The merge is
    a plain dict update, so on POSIX a lane declaring `Path` adds a second
    variable and really runs on the process PATH. Both branches on one machine,
    since a platform-gated assertion only exercises the half that machine runs."""
    spec = launch_spec(tmp_path, _lane(env=(("Path", "/opt/bin"),)))

    assert spec.path(windows=True) == "/opt/bin"
    assert spec.path(windows=False) is None


def test_the_exact_key_is_the_lanes_path_on_either_platform(tmp_path):
    spec = launch_spec(tmp_path, _lane(env=(("PATH", "/opt/bin"),)))

    assert spec.path(windows=True) == "/opt/bin"
    assert spec.path(windows=False) == "/opt/bin"


def test_a_lane_that_declares_no_path_leaves_the_process_one(tmp_path):
    assert launch_spec(tmp_path, _lane(env=(("CI", "1"),))).path() is None


def _runner_on(directory: Path) -> str:
    """An executable named the way this platform names one, and the word a lane
    would call it by."""
    directory.mkdir(parents=True, exist_ok=True)
    name = "suite.bat" if os.name == "nt" else "suite"
    runner = directory / name
    runner.write_text("", encoding="utf-8")
    runner.chmod(0o755)
    return name


def test_a_word_with_a_separator_resolves_from_the_lanes_cwd(tmp_path, monkeypatch):
    """The shell reads a path from the directory the lane runs in, and which()
    reads it from wherever this process stands."""
    repo = tmp_path / "repo"
    name = _runner_on(repo / "web" / "bin")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    word = os.path.join("bin", name)

    assert launch_spec(repo, _lane(cwd="web")).resolve(word) == str(repo / "web" / word)
    assert launch_spec(repo, _lane()).resolve(word) is None


def test_a_bare_name_resolves_on_the_lanes_own_path(tmp_path, monkeypatch):
    toolchain = tmp_path / "toolchain"
    name = _runner_on(toolchain)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    found = launch_spec(tmp_path, _lane(env=(("PATH", str(toolchain)),))).resolve(name)

    assert found is not None and Path(found).parent == toolchain
    assert launch_spec(tmp_path, _lane()).resolve(name) is None


# --- where cmd.exe finds a bare word --------------------------------------------
#
# cmd.exe looks in the directory it starts in before it reads PATH, unless the
# child's environment sets NoDefaultCurrentDirectoryInExePath. which() on
# Windows looks in this process's directory instead, so a bare `runcov` beside
# the lane passed doctor from the root and FAILed it from src/pkg. `windows=True`
# runs cmd.exe's search on any host: it reads nothing but the file system.

_CMD_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def _batch_file(directory: Path, stem: str) -> None:
    """`stem.BAT`, spelled the way PATHEXT spells the extension, so a
    case-sensitive file system finds it as cmd.exe would."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.BAT").write_text("@exit /b 0\n", encoding="utf-8")


@pytest.fixture
def cmd_env(tmp_path, monkeypatch):
    """A PATH holding nothing, the stock PATHEXT, and no opt-out of the
    directory search, whatever the machine running the test sets."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("PATHEXT", _CMD_PATHEXT)
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    return empty


def test_a_bare_word_in_the_lanes_cwd_resolves_wherever_this_process_stands(
        tmp_path, monkeypatch, cmd_env):
    repo = tmp_path / "repo"
    _batch_file(repo / "web", "runcov")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    spec = launch_spec(repo, _lane(cwd="web"))
    cmd_finds = str(repo / "web" / "runcov.BAT") if os.name == "nt" else None

    assert spec.resolve("runcov") == cmd_finds
    assert spec.resolve("runcov", windows=True) == str(repo / "web" / "runcov.BAT")


def test_a_bare_word_in_this_processs_directory_does_not_resolve(tmp_path, monkeypatch, cmd_env):
    """The empty and `.` PATH entries are cmd.exe's to read against the
    directory the lane starts in, not this one."""
    _batch_file(tmp_path / "doctor-cwd", "runcov")
    monkeypatch.chdir(tmp_path / "doctor-cwd")
    monkeypatch.setenv("PATH", ";".join(["", ".", str(cmd_env)]))
    (tmp_path / "repo").mkdir()
    spec = launch_spec(tmp_path / "repo", _lane())

    assert spec.resolve("runcov") is None
    assert spec.resolve("runcov", windows=True) is None


@pytest.mark.parametrize("declared_by", ["lane", "process"])
def test_no_default_current_directory_leaves_the_lanes_cwd_unsearched(
        tmp_path, monkeypatch, cmd_env, declared_by):
    _batch_file(tmp_path, "runcov")
    opt_out = ("NoDefaultCurrentDirectoryInExePath", "1")
    if declared_by == "process":
        monkeypatch.setenv(*opt_out)
    spec = launch_spec(tmp_path, _lane(env=(opt_out,) if declared_by == "lane" else ()))

    assert spec.resolve("runcov", windows=True) is None
    assert launch_spec(tmp_path, _lane()).resolve("runcov", windows=True) == \
        (None if declared_by == "process" else str(tmp_path / "runcov.BAT"))


def test_cmd_tries_each_pathext_extension_on_each_path_entry(tmp_path, cmd_env):
    toolchain = tmp_path / "toolchain"
    _batch_file(toolchain, "runcov")
    (tmp_path / "repo").mkdir()
    spec = launch_spec(tmp_path / "repo", _lane(env=(("Path", f'"{toolchain}"'),)))

    assert spec.resolve("runcov", windows=True) == str(toolchain / "runcov.BAT")
    assert spec.resolve("runcov.BAT", windows=True) == str(toolchain / "runcov.BAT")
    assert spec.resolve("runcov.exe", windows=True) is None


def test_a_child_environment_without_pathext_gets_cmds_default(tmp_path, monkeypatch, cmd_env):
    _batch_file(tmp_path, "runcov")
    monkeypatch.delenv("PATHEXT")

    assert launch_spec(tmp_path, _lane()).resolve("runcov", windows=True) == \
        str(tmp_path / "runcov.BAT")


def test_sh_reads_a_bare_name_from_path_alone(tmp_path, monkeypatch, cmd_env):
    """`windows=False` is sh's search: the lane's PATH, never its directory."""
    toolchain = tmp_path / "toolchain"
    name = _runner_on(toolchain)
    _runner_on(tmp_path)
    monkeypatch.chdir(cmd_env)

    found = launch_spec(tmp_path, _lane(env=(("PATH", str(toolchain)),))).resolve(name, windows=False)

    assert found is not None and Path(found).parent == toolchain
    assert launch_spec(tmp_path, _lane()).resolve(name, windows=False) is None


def test_a_relative_path_entry_is_read_from_the_lanes_cwd(tmp_path, cmd_env):
    repo = tmp_path / "repo"
    _batch_file(repo / "web" / "bin", "runcov")
    spec = launch_spec(repo, _lane(cwd="web", env=(("PATH", "bin"),
                                                   ("NoDefaultCurrentDirectoryInExePath", "1"))))

    assert spec.resolve("runcov", windows=True) == str(repo / "web" / "bin" / "runcov.BAT")
