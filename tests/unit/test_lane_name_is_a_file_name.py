"""A lane's name must work as a file name on every OS crapkit runs on.

crapkit writes a lane's log to .crapkit/lane-<name>.log, and nothing checked
the name at config load. A lane named `unit?` ended `crapkit coverage` in a
Windows OSError traceback at exit 1 while doctor passed it, and a lane named
`a:b` wrote its log into an NTFS alternate data stream of a file named
.crapkit/lane-a. The loader now refuses such a name at exit 3 on every OS, so
one crapkit.toml loads the same on Windows, macOS and Linux.
"""
from __future__ import annotations

import json

import pytest

from cli_inproc_repo import repo, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.config import load_config_text
from crapkit.errors import ConfigError

SCOPE = '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def lane(name: str, artifact: str = "cov.json") -> str:
    """One lane row; json.dumps spells the name as a TOML basic string."""
    return (f"[[lane]]\nname = {json.dumps(name)}\ncommand = \"python -c pass\"\n"
            f'artifact = "{artifact}"\nparser = "coveragepy"\nscopes = ["src"]\n')


@pytest.mark.parametrize("name, says", [
    ("unit?", r"holds \?"),
    ("a:b", "holds :"),
    ("py<3", "holds <"),
    ("py>3", "holds >"),
    ('say"hi"', 'holds "'),
    ("unit/fast", "holds /"),
    ("unit\\fast", r"holds \\"),
    ("a|b", r"holds \|"),
    ("all*", r"holds \*"),
    ("bell\x07", r"holds U\+0007"),
    ("tab\tlane", r"holds U\+0009"),
    ("a?b:c", r"holds : \?"),
])
def test_a_name_holding_a_character_windows_refuses_is_refused(name, says):
    with pytest.raises(ConfigError, match=says) as caught:
        load_config_text(SCOPE + lane(name))

    assert ".crapkit/lane-<name>.log" in str(caught.value)
    assert "rename the lane" in str(caught.value)


@pytest.mark.parametrize("name, device", [
    ("CON", "CON"), ("nul", "NUL"), ("Aux", "AUX"), ("PRN", "PRN"),
    ("COM1", "COM1"), ("com9", "COM9"), ("LPT1", "LPT1"), ("lpt0", "LPT0"),
    ("NUL.tests", "NUL"), ("con.unit.fast", "CON"), ("COM¹", "COM¹"),
    ("conin$", r"CONIN\$"),
])
def test_a_device_name_windows_reserves_is_refused_with_or_without_an_extension(name, device):
    with pytest.raises(ConfigError, match=f"{device} is a device name Windows reserves"):
        load_config_text(SCOPE + lane(name))


@pytest.mark.parametrize("name, end", [("unit.", "a dot"), ("unit ", "a space"),
                                       ("..", "a dot"), (".", "a dot")])
def test_a_name_ending_in_a_dot_or_a_space_is_refused(name, end):
    with pytest.raises(ConfigError, match=f"ends in {end}, which Windows drops"):
        load_config_text(SCOPE + lane(name))


def test_an_empty_name_is_refused():
    with pytest.raises(ConfigError, match="lane '': the name is empty"):
        load_config_text(SCOPE + lane(""))


@pytest.mark.parametrize("name", ["unit", "py-unit_2", "unit.fast", "unit tests", "CONSOLE",
                                  "COM10", "nul-check", "LPT", "café", ".hidden"])
def test_a_name_windows_can_use_loads(name):
    assert load_config_text(SCOPE + lane(name)).lanes[0].name == name


def test_two_names_that_differ_only_in_case_are_refused():
    """Windows and macOS fold case in file names, so `unit` and `Unit` would
    write one log file."""
    text = SCOPE + lane("unit", "a.json") + lane("Unit", "b.json")

    with pytest.raises(ConfigError, match="lanes 'unit' and 'Unit' differ only in case"):
        load_config_text(text)


def test_coverage_refuses_the_name_at_exit_3_before_any_lane_runs(repo, capsys):
    toml = repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('name = "unit"', 'name = "unit?"'),
                    encoding="utf-8")

    assert main(["coverage", "--repo", str(repo)]) == 3

    err = capsys.readouterr().err
    assert "lane 'unit?': the name holds ?" in err
    assert "Traceback" not in err
    assert not list((repo / ".crapkit").glob("lane-*")), "no lane ran, so no log was written"


def test_doctor_refuses_the_name_at_exit_3(repo, capsys):
    toml = repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('name = "unit"', 'name = "a:b"'),
                    encoding="utf-8")

    assert main(["doctor", "--repo", str(repo)]) == 3

    assert "lane 'a:b': the name holds :" in capsys.readouterr().err
