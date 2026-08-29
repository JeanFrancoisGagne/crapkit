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


# --- what a failing lane tells you about where the rest of the story is -------
#
# A reporter running 0.4.4 against a real repo saw only a 500-BYTE tail that
# began mid-line — "short test summary info ====" — with ten collection
# tracebacks cut off above it and nothing naming .crapkit/lane-py.log. Finding
# the log took another agent. Three things fix that: the path, a tail cut on
# line boundaries, and the reason lines when the end of the log has none.

def _plant_log(tmp_path, lines: list[str]) -> Path:
    log = tmp_path / ".crapkit" / "lane-py.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(lines), encoding="utf-8")
    return log


def _py_lane() -> Lane:
    return Lane(name="py", command="python -m pytest --cov", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("src",))


def _refusal(tmp_path, lines: list[str]) -> str:
    from crapkit.lanes import _raise_no_artifact

    log = _plant_log(tmp_path, lines)
    with pytest.raises(ToolError) as raised:
        _raise_no_artifact(_py_lane(), log, 2)
    return str(raised.value)


def test_the_refusal_names_the_log_file_that_holds_the_whole_story(tmp_path):
    message = _refusal(tmp_path, ["$ python -m pytest --cov", "boom", "(exit 2)"])

    assert str(tmp_path / ".crapkit" / "lane-py.log") in message


def test_a_lane_that_runs_and_writes_nothing_names_its_log_too(tmp_path):
    """Through the real runner, not just the message builder."""
    lane = Lane(name="dead", command=f'"{sys.executable}" -c "import sys; sys.exit(9)"',
                artifact="cov.json", parser="istanbul", scopes=())
    with pytest.raises(ToolError) as raised:
        run_lane(tmp_path, lane)

    assert str(tmp_path / ".crapkit" / "lane-dead.log") in str(raised.value)


def test_the_tail_begins_at_a_line_boundary(tmp_path):
    """A byte tail starts mid-line, and the reader cannot tell that fragment
    from the real first word of the message."""
    lines = [f"line {i:03d} " + "x" * 60 for i in range(40)]
    message = _refusal(tmp_path, lines)

    shown = message.split("; last output: ", 1)[1].splitlines()
    assert shown, "the tail is the point of the message"
    assert set(shown) <= set(lines), "a shown line is a whole line of the log"
    assert shown[-1] == lines[-1]


def test_the_reason_survives_a_summary_block_long_enough_to_bury_it(tmp_path):
    """pytest ends on a summary saying WHICH files broke and never why. The
    reason sits above it, and on a wide enough collection failure the byte
    budget never reaches it."""
    lines = ["$ python -m pytest --cov",
             "tests/test_a.py:3: in <module>",
             "    from faro.core import Widget",
             "E   ImportError: cannot import name 'Widget' from '/other/checkout/src'",
             "=========================== short test summary info ============================"]
    lines += [f"ERROR tests/test_{i:03d}.py" for i in range(60)]
    lines += ["============================== 60 errors in 0.6s ===============================",
              "(exit 2)"]

    message = _refusal(tmp_path, lines)

    assert "cannot import name 'Widget' from '/other/checkout/src'" in message
    assert "short test summary info" not in message, "the budget went to the reason"


def test_a_tail_that_already_says_why_is_not_repeated(tmp_path):
    lines = ["$ python -m pytest --cov", "E   ImportError: no module named 'faro'", "(exit 2)"]

    message = _refusal(tmp_path, lines)

    assert message.count("no module named 'faro'") == 1


def test_the_pytest_cov_hint_still_fires_through_the_new_tail(tmp_path):
    lines = ["$ python -m pytest --cov",
             "pytest: error: unrecognized arguments: --cov --cov-branch", "(exit 4)"]

    assert "pip install pytest-cov" in _refusal(tmp_path, lines)


def test_one_line_longer_than_the_budget_says_it_was_cut(tmp_path):
    message = _refusal(tmp_path, ["y" * 4000])

    assert "; last output: ..." in message, "an ellipsis marks the one cut a tail cannot avoid"


def test_a_refusal_with_no_log_at_all_still_names_where_it_would_be(tmp_path):
    """Nothing writes the log before the command runs, and a lane refused ahead
    of that has no tail to quote. The path is the half that always applies."""
    from crapkit.lanes import _raise_no_artifact

    missing = tmp_path / ".crapkit" / "lane-py.log"
    with pytest.raises(ToolError) as raised:
        _raise_no_artifact(_py_lane(), missing, None)

    assert str(missing) in str(raised.value)
    assert "last output" not in str(raised.value), "no log, nothing to quote"
