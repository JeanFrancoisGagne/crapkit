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


def test_a_lane_that_left_coverage_shards_is_told_they_are_there(tmp_path):
    """coverage.py in parallel mode writes one `.coverage.<host>.<pid>.<rand>`
    per process and merges them only at the end, so a run that was killed leaves
    every measurement on disk and no JSON. One reporter combined them by hand
    and got a usable artifact; crapkit said only that the artifact was missing,
    and the shards sit a directory above the path it names."""
    (tmp_path / ".coverage.box.pid5.aaaa").write_text("x", encoding="utf-8")
    (tmp_path / ".coverage.box.pid6.bbbb").write_text("x", encoding="utf-8")
    lane = Lane(name="py", command=f'"{PY}" -c "import sys; sys.exit(1)"',
                artifact=".crapkit/cov/coverage.json", parser="coveragepy", scopes=())

    with pytest.raises(ToolError) as exc:
        run_lane(tmp_path, lane)

    message = str(exc.value)
    assert "2 coverage shards" in message
    assert "coverage combine" in message and ".crapkit/cov/coverage.json" in message
    assert "--reuse-artifacts" in message


def test_the_shard_hint_looks_in_the_directory_the_lane_ran_in(tmp_path):
    """A lane with a `cwd` writes its shards there, not at the repo root."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / ".coverage.box.pid5.aaaa").write_text("x", encoding="utf-8")
    lane = Lane(name="py", command=f'"{PY}" -c "import sys; sys.exit(1)"',
                artifact="cov.json", parser="coveragepy", scopes=(), cwd="pkg")

    with pytest.raises(ToolError, match="1 coverage shard") as exc:
        run_lane(tmp_path, lane)

    assert str(tmp_path / "pkg") in str(exc.value)


def test_a_lane_with_no_shards_gets_no_salvage_hint(tmp_path):
    """The hint is for the one failure it fits. Printing it on every missing
    artifact is how a reader learns to skip the end of the message."""
    lane = Lane(name="py", command=f'"{PY}" -c "import sys; sys.exit(1)"',
                artifact="cov.json", parser="coveragepy", scopes=())

    with pytest.raises(ToolError) as exc:
        run_lane(tmp_path, lane)

    assert "coverage shard" not in str(exc.value)


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
