"""Lane runner seam: config lane in, coverage + provenance out; failures carry the command's voice."""
import sys
from pathlib import Path

import pytest

import crapkit
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import run_lane


def test_failing_lane_error_carries_stderr_tail_and_log_file(tmp_path):
    lane = Lane(name="bad", command=f'"{sys.executable}" -c "import sys; sys.stderr.write(\'boom: registry exploded\'); sys.exit(2)"',
                artifact="cov.json", parser="istanbul", scopes=())
    with pytest.raises(ToolError, match="registry exploded"):
        run_lane(tmp_path, lane)
    log = (tmp_path / ".crapkit" / "lane-bad.log").read_text(encoding="utf-8")
    assert "registry exploded" in log


def test_lane_env_reaches_the_command(tmp_path):
    py = sys.executable
    cmd = f'"{py}" -c "import os,json,pathlib; pathlib.Path(\'cov.json\').write_text(json.dumps({{os.environ[\'CRAPKIT_PROBE\']: {{}}}}))"'
    lane = Lane(name="envy", command=cmd, artifact="cov.json", parser="istanbul", scopes=(),
                env=(("CRAPKIT_PROBE", "C:\\x\\probe.ts"),))
    coverage, prov, _ = run_lane(tmp_path, lane)
    assert prov["exit_code"] == 0


def test_coveragepy_lane_refuses_to_run_inside_a_container(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "1")
    lane = Lane(name="py", command="python -m pytest", artifact="cov.json", parser="coveragepy", scopes=())
    with pytest.raises(ToolError, match="host-only"):
        run_lane(tmp_path, lane)


def test_container_ok_lane_is_allowed_through_the_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "1")
    lane = Lane(name="py", command="python -c \"print(1)\"", artifact="missing.json",
                parser="coveragepy", scopes=(), container_ok=True)
    with pytest.raises(ToolError, match="no artifact"):
        run_lane(tmp_path, lane)


def test_no_private_project_namespace_ships_in_the_library():
    """A general-purpose tool cannot gate on another project's env vars: nobody
    outside that project will ever set one, so the switch is dead on arrival."""
    package = Path(crapkit.__file__).parent
    leaked = sorted(p.name for p in package.glob("*.py")
                    if "COVMARK" in p.read_text(encoding="utf-8"))

    assert leaked == []
