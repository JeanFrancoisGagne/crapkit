"""The lane run and its flake retest start their command through the launch spec.

lanes.py built the child's cwd and env in a private helper, and the retest
patched that dict by hand before adding its own variables. Both take
lane_command.launch_spec's popen kwargs now. A command that goes silent past
no_progress_seconds is killed on either path: the run names the stall, and the
retest keeps every test it was asked about failed.
"""
import pytest

from crapkit import lanes
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.procs import NoProgress


def _lane() -> Lane:
    return Lane(name="py", command="python -m pytest --cov", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("src",), cwd="web",
                env=(("CRAPKIT_LANE_VAR", "lane"),), results_artifact=".crapkit/cov/junit.xml",
                retest_command="python -m pytest {tests}", no_progress_seconds=5)


def _silent(seen: list) -> object:
    """A run_bounded whose command never writes a byte: the stall it reports is
    the idle deadline the lane asked for."""
    def run_bounded(command, timeout, **kwargs):
        seen.append(kwargs)
        raise NoProgress(kwargs["no_progress"])
    return run_bounded


def test_a_silent_lane_is_killed_and_named_from_its_own_directory(tmp_path, monkeypatch):
    (tmp_path / "web").mkdir()
    seen: list = []
    monkeypatch.setattr(lanes, "run_bounded", _silent(seen))

    with pytest.raises(ToolError, match=r"lane 'py' wrote no output for 5s \(attempt 1\)"):
        lanes.run_lane(tmp_path, _lane())

    (started,) = seen
    assert started["cwd"] == tmp_path / "web"
    assert started["env"]["CRAPKIT_LANE_VAR"] == "lane"
    log = (tmp_path / ".crapkit" / "lane-py.log").read_text(encoding="utf-8")
    assert "[crapkit] no output for 5s; killed" in log


def test_a_silent_retest_keeps_every_test_failed(tmp_path, monkeypatch):
    (tmp_path / "web").mkdir()
    seen: list = []
    monkeypatch.setattr(lanes, "run_bounded", _silent(seen))

    assert lanes.retest_lane(tmp_path, _lane(), {"tests/test_a.py::test_x"}) == set()

    (started,) = seen
    assert started["cwd"] == tmp_path / "web"
    assert started["env"]["CRAPKIT_LANE_VAR"] == "lane", "the retest keeps the lane's own env"
