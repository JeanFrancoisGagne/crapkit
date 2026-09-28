"""A lane that wraps its runner in `bash -c` or `sh -c` is judged by the
commands the payload holds.

The full-suite guard read `bash -c "..."` as three words and saw no pytest, so
a lane that narrows its suite loaded with no refusal. Under cmd.exe a
single-quoted payload split at every space, and a whole-suite lane was refused
on `tests'`, a token bash never hands pytest. bash.exe reads the line cmd.exe
hands it with sh's own quote rules, and the payload is sh text either way, so
the payload is read with sh's rules on both OSes. A payload sh cannot split is
marked, and doctor WARNs from the mark.
"""
from pathlib import Path

import pytest

from crapkit import config as config_module
from crapkit.config import load_config_text
from crapkit.errors import ConfigError
from crapkit.lane_command import command_steps, pytest_python, pytest_step
from cli_inproc_repo import repo, template_repo  # noqa: F401

PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
REPORT = "--cov=pkg --cov-report=json:.crapkit/cov/py.json"


def _toml(command: str, parser: str = "coveragepy") -> str:
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    return ('[[scope]]\nname = "py"\npaths = ["pkg"]\nlanguages = ["python"]\n'
            f'[[lane]]\nname = "py"\ncommand = "{escaped}"\n'
            f'artifact = "cov.json"\nparser = "{parser}"\nscopes = ["py"]\n')


def _load(root: Path, command: str, parser: str = "coveragepy"):
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    return load_config_text(_toml(command, parser), root=root)


SHELLS = [pytest.param(True, id="cmd"), pytest.param(False, id="sh")]


@pytest.mark.parametrize("shell_is_cmd", SHELLS)
@pytest.mark.parametrize("command", [
    f'bash -c "python -m pytest tests/test_m.py {REPORT}"',
    f"bash -c 'python -m pytest tests/test_m.py {REPORT}'",
    f"sh -c 'python -m pytest tests/test_m.py {REPORT}'",
    f"bash -lc 'python -m pytest tests/test_m.py {REPORT}'",
    f"bash -e -c 'cd .; python -m pytest tests/test_m.py {REPORT}'",
    f'bash -c "cd . && python -m pytest tests/test_m.py {REPORT}"',
    f"/usr/bin/bash -o pipefail -c 'python -m pytest tests/test_m.py {REPORT}; echo done'",
    f"bash --login -c -- 'python -m pytest tests/test_m.py {REPORT}'",
])
def test_a_payload_that_narrows_the_suite_is_refused(tmp_path, monkeypatch, shell_is_cmd, command):
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", shell_is_cmd)

    with pytest.raises(ConfigError, match="positional argument 'tests/test_m.py' narrows") as caught:
        _load(tmp_path, command)

    assert "double quotes" not in str(caught.value), "the payload was read with sh's quotes"


@pytest.mark.parametrize("shell_is_cmd", SHELLS)
@pytest.mark.parametrize("command", [
    f"bash -c 'python -m pytest {REPORT} tests'",
    f'bash -c "python -m pytest {REPORT} tests"',
    f"bash -c 'python -m pytest -k \"not slow\" {REPORT}'",
    f"sh -c 'python -m pytest -m \"not live and not perf\" {REPORT}'",
])
def test_a_payload_that_runs_the_whole_suite_loads(tmp_path, monkeypatch, shell_is_cmd, command):
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", shell_is_cmd)

    assert _load(tmp_path, command).lanes[0].full_suite is True


@pytest.mark.parametrize("shell_is_cmd", SHELLS)
def test_a_vitest_filter_inside_a_payload_is_refused(tmp_path, monkeypatch, shell_is_cmd):
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", shell_is_cmd)

    with pytest.raises(ConfigError, match="file filter 'src/a.ts'"):
        _load(tmp_path, "sh -c 'npx vitest run src/a.ts --coverage'", parser="istanbul")


@pytest.mark.parametrize("cmd", [True, False])
def test_the_python_probed_for_pytest_cov_is_the_one_inside_the_payload(cmd):
    """doctor's pytest-cov probe and the lanes hint read pytest_python: it
    answered None for the payload, and doctor said the lane runs pytest
    through `bash`."""
    command = 'bash -c "python -m pytest --cov=pkg"'
    assert pytest_step(command, cmd=cmd) == ["python", "-m", "pytest", "--cov=pkg"]
    assert pytest_python(command, cmd=cmd) == "python"


@pytest.mark.parametrize("cmd", [True, False])
def test_a_shell_running_a_file_is_not_descended(cmd):
    """`bash run.sh` runs a file, not a payload, so its words stay its own."""
    steps = command_steps("bash run_tests.sh --cov && sh -x", cmd=cmd)
    assert [step.words for step in steps.steps] == [("bash", "run_tests.sh", "--cov"),
                                                    ("sh", "-x")]
    assert steps.unreadable == ()
    assert command_steps('bash -c ""', cmd=cmd) == ((((), False),), ())


@pytest.mark.parametrize("cmd", [True, False])
def test_a_nested_payload_is_read_down_to_the_runner(cmd):
    command = """bash -c "sh -c 'python -m pytest tests/unit'" """
    assert [step.words for step in command_steps(command, cmd=cmd).steps] == \
        [("python", "-m", "pytest", "tests/unit")]


@pytest.mark.parametrize("cmd", [True, False])
def test_a_payload_sh_cannot_split_is_marked_and_the_line_still_loads(tmp_path, monkeypatch, cmd):
    """A quote that never closes inside the payload: the guard cannot see into
    it, so it judges nothing there and says so through the mark."""
    command = """bash -c "python -m pytest 'tests/test_m.py --cov=pkg" """
    steps = command_steps(command, cmd=cmd)

    assert steps.unreadable == ("bash -c \"python -m pytest 'tests/test_m.py --cov=pkg\"",)
    assert [step.words[:2] for step in steps.steps] == [("bash", "-c")]
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", cmd)
    assert _load(tmp_path, command).lanes


def test_under_cmd_a_single_quote_outside_any_payload_still_earns_the_hint(monkeypatch):
    """cmd.exe hands pytest itself `'not` and `perf'`, so the hint stays for
    the step cmd.exe reads, and only for it."""
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", True)
    with pytest.raises(ConfigError, match="double quotes"):
        load_config_text(_toml("python -m pytest -m 'not live and not perf' --cov=pkg"))


def test_doctor_warns_that_nothing_inside_an_unsplittable_payload_was_read(repo, capsys):
    """The guard cannot see into the payload, and doctor says so."""
    import json

    from crapkit.cli import main

    toml = repo / "crapkit.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace('command = "python -c pass"',
                                 """command = "bash -c \\"python -c 'pass\\"" """.strip(), 1),
                    encoding="utf-8")

    assert main(["doctor", "--json", "--repo", str(repo)]) in (0, 1)

    warnings = json.loads(capsys.readouterr().out)["warnings"]
    assert [w for w in warnings if "sh cannot split" in w] == [
        "lane 'unit': sh cannot split the script in `bash -c \"python -c 'pass\"`, since a "
        "quote or an escape in it never closes, so crapkit reads nothing inside it: the "
        "full-suite guard, the pytest-cov probe and the coverage data-file check all pass it "
        "unjudged; fix its quoting"]
