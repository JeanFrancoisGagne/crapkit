"""The full-suite guard and pytest's own `testpaths`.

`python -m pytest tests --cov=app` with `testpaths = ["tests"]` runs the whole
suite: the positional names exactly what a bare `pytest` collects. The guard
read it as narrowing and every command that loads the configuration exited 3
with `positional argument 'tests' narrows a full-suite coverage run`. Given the
repository root, the loader reads pytest's configuration the way pytest does
and lets a positional equal to a configured testpaths entry through.
"""
from pathlib import Path

import pytest

from crapkit import config as config_module
from crapkit.config import load_config_text, pytest_testpaths_at
from crapkit.errors import ConfigError

PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'


def _toml(command: str, cwd: str = "") -> str:
    lane_cwd = f'cwd = "{cwd}"\n' if cwd else ""
    return ('[[scope]]\nname = "py"\npaths = ["app"]\nlanguages = ["python"]\n'
            f'[[lane]]\nname = "py"\ncommand = "{command}"\n{lane_cwd}'
            'artifact = "cov.json"\nparser = "coveragepy"\nscopes = ["py"]\n')


def _write(root: Path, name: str, text: str) -> None:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")


# --- the guard, given a root --------------------------------------------------

def test_a_positional_equal_to_a_configured_testpath_is_not_narrowing(tmp_path):
    _write(tmp_path, "pyproject.toml", PYPROJECT)

    cfg = load_config_text(_toml("python -m pytest tests --cov=app"), root=tmp_path)

    assert cfg.lanes[0].full_suite is True, "the lane stays a full-suite lane"


def test_without_a_root_the_guard_reads_no_testpaths_and_refuses_as_before():
    with pytest.raises(ConfigError, match="positional argument 'tests' narrows a full-suite"):
        load_config_text(_toml("python -m pytest tests --cov=app"))


def test_a_positional_the_configured_testpaths_do_not_name_still_narrows(tmp_path):
    _write(tmp_path, "pyproject.toml", PYPROJECT)

    with pytest.raises(ConfigError, match="positional argument 'tests/unit' narrows"):
        load_config_text(_toml("python -m pytest tests/unit --cov=app"), root=tmp_path)


def test_a_positional_beside_a_configured_one_is_the_one_refused(tmp_path):
    _write(tmp_path, "pyproject.toml", PYPROJECT)

    with pytest.raises(ConfigError, match="positional argument 'app/hot.py' narrows"):
        load_config_text(_toml("python -m pytest tests app/hot.py --cov=app"), root=tmp_path)


def test_a_root_without_any_pytest_configuration_refuses_as_before(tmp_path):
    with pytest.raises(ConfigError, match="positional argument 'tests' narrows"):
        load_config_text(_toml("python -m pytest tests --cov=app"), root=tmp_path)


@pytest.mark.parametrize("spelling", ["tests/", "./tests", "tests\\\\"])
def test_a_testpath_spelled_with_a_separator_still_names_it(tmp_path, spelling: str):
    _write(tmp_path, "pyproject.toml", PYPROJECT)

    cfg = load_config_text(_toml(f"python -m pytest {spelling} --cov=app"), root=tmp_path)

    assert cfg.lanes[0].name == "py"


def test_a_lane_with_a_cwd_reads_the_testpaths_of_its_own_directory(tmp_path):
    """The command runs in `cwd`, so that is where pytest picks its inifile
    and what the positional is relative to."""
    _write(tmp_path, "backend/pyproject.toml", PYPROJECT)

    cfg = load_config_text(_toml("python -m pytest tests --cov=app", cwd="backend"),
                           root=tmp_path)

    assert cfg.lanes[0].cwd == "backend"


def test_the_root_s_testpaths_do_not_speak_for_a_lane_run_elsewhere(tmp_path):
    _write(tmp_path, "pyproject.toml", PYPROJECT)

    with pytest.raises(ConfigError, match="positional argument 'tests' narrows"):
        load_config_text(_toml("python -m pytest tests --cov=app", cwd="backend"),
                         root=tmp_path)


def test_a_command_without_a_positional_reads_no_file(tmp_path, monkeypatch):
    """Read-only commands load the configuration too. The common lane costs
    them no file read: pytest's files are opened only to judge a positional."""
    def never(_directory):
        raise AssertionError("pytest's configuration was read with nothing to judge")
    monkeypatch.setattr(config_module, "pytest_testpaths_at", never)

    cfg = load_config_text(_toml("python -m pytest --cov=app -n 8"), root=tmp_path)

    assert cfg.lanes[0].name == "py"


def test_a_scoped_lane_reads_no_file_either(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "pytest_testpaths_at",
                        lambda _d: (_ for _ in ()).throw(AssertionError("read")))

    cfg = load_config_text(_toml("python -m pytest tests --cov=app") + "full_suite = false\n",
                           root=tmp_path)

    assert cfg.lanes[0].full_suite is False


# --- the positionals have to cover every configured entry ---------------------

TWO_ENTRIES = '[tool.pytest.ini_options]\ntestpaths = ["tests", "integration"]\n'


def test_one_entry_of_several_still_narrows_the_run(tmp_path):
    """`testpaths = ["tests", "integration"]`: a bare `pytest` collects both, so
    `pytest tests` runs half the suite, the run-order narrowing the guard exists
    to refuse. Naming one configured entry is not enough; together the
    positionals have to name every entry."""
    _write(tmp_path, "pyproject.toml", TWO_ENTRIES)

    with pytest.raises(ConfigError, match="positional argument 'tests' narrows"):
        load_config_text(_toml("python -m pytest tests --cov=app"), root=tmp_path)


def test_every_entry_named_together_leaves_only_the_extra_to_refuse(tmp_path):
    _write(tmp_path, "pyproject.toml", TWO_ENTRIES)

    with pytest.raises(ConfigError, match="positional argument 'app/hot.py' narrows"):
        load_config_text(_toml("python -m pytest tests integration app/hot.py --cov=app"),
                         root=tmp_path)


# --- pytest_testpaths_at: the files pytest reads, in pytest's order ------------

@pytest.mark.parametrize("filename,text", [
    ("pytest.ini", "[pytest]\ntestpaths = tests integration\n"),
    (".pytest.ini", "[pytest]\ntestpaths = tests integration\n"),
    ("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests", "integration"]\n'),
    ("tox.ini", "[pytest]\ntestpaths =\n    tests\n    integration\n"),
    ("setup.cfg", "[tool:pytest]\ntestpaths = tests integration\n"),
])
def test_every_file_pytest_reads_testpaths_from_is_read(tmp_path, filename: str, text: str):
    _write(tmp_path, filename, text)

    assert pytest_testpaths_at(tmp_path) == ("tests", "integration")
    cfg = load_config_text(_toml("python -m pytest tests integration --cov=app"), root=tmp_path)
    assert cfg.lanes[0].name == "py"


def test_a_pyproject_string_value_is_split_the_way_pytest_splits_it(tmp_path):
    _write(tmp_path, "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = "tests integration"\n')

    assert pytest_testpaths_at(tmp_path) == ("tests", "integration")


@pytest.mark.parametrize("filename", ["pytest.ini", ".pytest.ini"])
def test_an_empty_pytest_ini_decides_and_names_no_testpaths(tmp_path, filename: str):
    """pytest.ini and .pytest.ini take precedence over every other file even
    when empty: beside one, a bare `pytest` collects from the rootdir and never
    reads the pyproject below it, so `pytest tests` narrows that run."""
    _write(tmp_path, filename, "")
    _write(tmp_path, "pyproject.toml", PYPROJECT)

    assert pytest_testpaths_at(tmp_path) == ()
    with pytest.raises(ConfigError, match="narrows"):
        load_config_text(_toml("python -m pytest tests --cov=app"), root=tmp_path)


def test_a_pytest_ini_holding_a_pytest_section_without_testpaths_decides_too(tmp_path):
    """pytest picks one inifile and never consults a lower-ranked one: a
    `[pytest]` naming no testpaths means a bare `pytest` collects from the
    rootdir, and the pyproject below it is never read."""
    _write(tmp_path, "pytest.ini", "[pytest]\naddopts = -q\n")
    _write(tmp_path, "pyproject.toml", PYPROJECT)

    assert pytest_testpaths_at(tmp_path) == ()
    with pytest.raises(ConfigError, match="narrows"):
        load_config_text(_toml("python -m pytest tests --cov=app"), root=tmp_path)


def test_the_other_files_decide_only_when_they_hold_a_pytest_section(tmp_path):
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'app'\n")
    _write(tmp_path, "tox.ini", "[tox]\nenvlist = py311\n")
    _write(tmp_path, "setup.cfg", "[tool:pytest]\ntestpaths = tests\n")

    assert pytest_testpaths_at(tmp_path) == ("tests",)


def test_a_file_that_does_not_parse_reads_as_no_section(tmp_path):
    _write(tmp_path, "pytest.ini", "]]] not ini")
    _write(tmp_path, "pyproject.toml", "[[[")
    _write(tmp_path, "setup.cfg", "[tool:pytest]\ntestpaths = tests\n")

    assert pytest_testpaths_at(tmp_path) == ("tests",)


def test_a_directory_with_no_pytest_configuration_names_no_testpaths(tmp_path):
    assert pytest_testpaths_at(tmp_path) == ()
    assert pytest_testpaths_at(tmp_path / "missing") == ()
