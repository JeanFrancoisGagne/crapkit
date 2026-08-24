"""Lane infrastructure: streamed logs, crapkit-owned timeouts, retries. Real subprocesses."""
import json
import sys

import pytest

from crapkit.config import Lane, load_config_text
from crapkit.errors import ConfigError, ToolError
from crapkit.lanes import run_lane

PY = sys.executable

MINIMAL_ART = json.dumps({"C:/r/src/a.ts": {"fnMap": {}, "f": {}, "branchMap": {}, "b": {}}})


def test_lane_timeout_kills_the_command_and_says_so(tmp_path):
    lane = Lane(name="slow", command=f'"{PY}" -c "import time; time.sleep(30)"',
                artifact="cov.json", parser="istanbul", scopes=(), timeout_seconds=1)
    with pytest.raises(ToolError, match="timed out"):
        run_lane(tmp_path, lane)


def test_lane_retries_recover_a_flaky_command(tmp_path):
    # First attempt plants a marker and dies without an artifact; the second
    # sees the marker and writes a valid artifact. retries=1 must recover.
    script = (
        "import pathlib, json, sys; m = pathlib.Path('marker'); "
        "(pathlib.Path('cov.json').write_text(json.dumps({'C:/r/a.ts': {'fnMap': {}, 'f': {}, 'branchMap': {}, 'b': {}}})), sys.exit(0)) "
        "if m.exists() else (m.write_text('x'), sys.exit(3))"
    )
    lane = Lane(name="flaky", command=f'"{PY}" -c "{script}"',
                artifact="cov.json", parser="istanbul", scopes=(), retries=1)
    coverage, prov, _ = run_lane(tmp_path, lane)
    assert prov["exit_code"] == 0
    log = (tmp_path / ".crapkit" / "lane-flaky.log").read_text(encoding="utf-8")
    assert "attempt 2" in log, "the retry must be visible in the lane log"


def test_lane_log_streams_the_command_header_even_on_failure(tmp_path):
    lane = Lane(name="dead", command=f'"{PY}" -c "import sys; sys.exit(9)"',
                artifact="cov.json", parser="istanbul", scopes=())
    with pytest.raises(ToolError, match="no artifact"):
        run_lane(tmp_path, lane)
    log = (tmp_path / ".crapkit" / "lane-dead.log").read_text(encoding="utf-8")
    assert log.startswith("$ "), "the log opens with the command that ran"


def test_config_parses_timeout_and_retries():
    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
        '[[lane]]\nname = "py"\ncommand = "pytest --cov"\nartifact = "c.json"\n'
        'parser = "coveragepy"\nscopes = ["src"]\ntimeout_seconds = 600\nretries = 2\n')
    assert cfg.lanes[0].timeout_seconds == 600 and cfg.lanes[0].retries == 2


def test_config_rejects_negative_timeout_or_retries():
    base = ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
            '[[lane]]\nname = "py"\ncommand = "pytest --cov"\nartifact = "c.json"\n'
            'parser = "coveragepy"\nscopes = ["src"]\n')
    with pytest.raises(ConfigError, match="timeout_seconds"):
        load_config_text(base + "timeout_seconds = -5\n")
    with pytest.raises(ConfigError, match="retries"):
        load_config_text(base + "retries = -1\n")
