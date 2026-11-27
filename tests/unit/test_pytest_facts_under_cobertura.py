"""A pytest lane keeps its runner facts whatever its parser, and only the
coveragepy format brings coverage.py's producer facts.

The runner facts follow the command (toolchain's spelled runner): the
container refusal, doctor's pytest-cov probe, the narrowing refusal under
full_suite and the junit hint. The data-file finding and the shard recipe
follow the format, so a pytest lane writing Cobertura has neither.
"""
import pytest

from crapkit import lanes
from crapkit.cli.admin import NO_PACKAGES, _junit_hint, _probed_lanes
from crapkit.config import load_config_text
from crapkit.doctor import _coverage_data_files
from crapkit.errors import ConfigError, ToolError

COMMAND = "python -m pytest --cov --cov-report=xml:.crapkit/cov/py.xml"
PARSERS = ["coveragepy", "cobertura"]


def _config(parser: str, command: str = COMMAND, extra: str = ""):
    return load_config_text(
        '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "app"\npaths = ["app"]\n'
        'languages = ["python"]\n\n[[lane]]\nname = "py"\n'
        f'command = "{command}"\nartifact = ".crapkit/cov/py.xml"\nparser = "{parser}"\n'
        f'scopes = ["app"]\n{extra}')


@pytest.mark.parametrize("parser", PARSERS)
def test_the_container_refusal_fires_without_container_ok(parser, monkeypatch):
    (lane,) = _config(parser).lanes
    monkeypatch.setattr(lanes, "_in_container", lambda: True)

    with pytest.raises(ToolError) as refused:
        lanes._refuse_container_python(lane)

    assert "runs the python suite, which is host-only" in str(refused.value)


@pytest.mark.parametrize("parser", PARSERS)
def test_doctor_probes_pytest_cov_for_the_lane(parser):
    cfg = _config(parser)

    assert _probed_lanes(cfg.lanes) == list(cfg.lanes)


@pytest.mark.parametrize("parser", PARSERS)
def test_a_positional_narrowing_under_full_suite_exits_3(parser):
    with pytest.raises(ConfigError) as refused:
        _config(parser, command=COMMAND + " tests/unit")

    assert refused.value.exit_code == 3


@pytest.mark.parametrize("parser", PARSERS)
def test_the_junit_hint_names_junitxml(parser):
    (lane,) = _config(parser).lanes

    assert "--junitxml=" in _junit_hint(lane, NO_PACKAGES)


def test_only_the_coveragepy_format_brings_the_data_file_finding():
    """coverage.py's data file is a coveragepy fact; under cobertura doctor
    has no data file to compare, so it makes no finding."""
    assert _coverage_data_files(_config("coveragepy").lanes) != {}
    assert _coverage_data_files(_config("cobertura").lanes) == {}


def test_only_the_coveragepy_format_brings_the_shard_recipe(tmp_path):
    (tmp_path / ".coverage.host.1.abc").write_bytes(b"")

    assert "coverage combine" in lanes._shard_hint(tmp_path, _config("coveragepy").lanes[0])
    assert lanes._shard_hint(tmp_path, _config("cobertura").lanes[0]) == ""
