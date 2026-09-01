"""Lane runner seam: config lane in, coverage + provenance out; failures carry the command's voice."""
import json
import sys
from pathlib import Path

import pytest

import crapkit
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import run_lane

COVERAGEPY = json.dumps({
    "meta": {"branch_coverage": True},
    "files": {"src/a.py": {"functions": {"hot": {
        "start_line": 1, "executed_lines": [1, 2], "missing_lines": [],
        "summary": {"covered_lines": 2, "num_statements": 2,
                    "num_branches": 2, "covered_branches": 2}}}}},
})

ISTANBUL = json.dumps({"C:/r/src/a.ts": {"fnMap": {}, "f": {}, "branchMap": {}, "b": {}}})


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


def test_reuse_artifacts_reads_a_coveragepy_artifact_inside_a_container(tmp_path, monkeypatch):
    """The guard names an OOM that the reuse path cannot reach: `--reuse-artifacts`
    launches nothing and only parses the file already on disk. crapkit ships a
    Dockerfile and its own action's verdict step is `verify --reuse-artifacts`,
    so a container reading host-built artifacts is the shape users land in."""
    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "1")
    (tmp_path / "cov.json").write_text(COVERAGEPY, encoding="utf-8")
    lane = Lane(name="py", command="python -m pytest", artifact="cov.json",
                parser="coveragepy", scopes=())

    coverage, _, _ = run_lane(tmp_path, lane, reuse_artifact=True)

    assert list(coverage) == ["src/a.py"]


# --- the artifact a run wrote, not the one a previous run left ---------------
#
# Existence was the whole check, so a lane failed loud exactly once: on the
# first run, against an empty .crapkit/. Every run after that scored the
# previous run's file. A vitest lane without reportOnFailure and a pytest lane
# dying in collection both land here, and the grade that comes out looks like an
# answer.

def _dead_lane(tmp_path, name: str = "py") -> Lane:
    return Lane(name=name, command=f'"{sys.executable}" -c "import sys; sys.exit(2)"',
                artifact="cov.json", parser="istanbul", scopes=())


def test_a_run_that_wrote_nothing_does_not_inherit_the_last_run_s_artifact(tmp_path):
    (tmp_path / "cov.json").write_text(ISTANBUL, encoding="utf-8")

    with pytest.raises(ToolError, match="predates") as raised:
        run_lane(tmp_path, _dead_lane(tmp_path))

    assert "cov.json" in str(raised.value)
    assert "command exit 2" in str(raised.value), "the exit code the run really had"


def test_the_stale_artifact_refusal_still_carries_the_log_and_its_tail(tmp_path):
    """Same refusal, so it keeps the same evidence: a reader told the file is
    the previous run's still needs the log that says why this one wrote none."""
    (tmp_path / "cov.json").write_text(ISTANBUL, encoding="utf-8")
    lane = Lane(name="py", command=f'"{sys.executable}" -c '
                                   '"import sys; sys.stderr.write(\'boom: no tests ran\')'
                                   '; sys.exit(2)"',
                artifact="cov.json", parser="istanbul", scopes=())

    with pytest.raises(ToolError) as raised:
        run_lane(tmp_path, lane)

    assert str(tmp_path / ".crapkit" / "lane-py.log") in str(raised.value)
    assert "no tests ran" in str(raised.value)


def test_a_rerun_that_writes_the_same_bytes_is_still_this_run_s_artifact(tmp_path):
    """Freshness is the mtime, not the content: a runner that rewrites an
    identical report bumps it, so a byte-identical rerun must stay green."""
    (tmp_path / "cov.json").write_text(ISTANBUL, encoding="utf-8")
    script = tmp_path / "write.py"
    script.write_text(f"open('cov.json', 'w', encoding='utf-8').write({ISTANBUL!r})\n",
                      encoding="utf-8")
    lane = Lane(name="py", command=f'"{sys.executable}" "{script}"',
                artifact="cov.json", parser="istanbul", scopes=())

    _, prov, _ = run_lane(tmp_path, lane)

    assert prov["exit_code"] == 0


def test_a_junit_the_run_did_not_rewrite_is_the_previous_run_s_too(tmp_path):
    """The results file is read by the same existence test, so a suite that
    wrote fresh coverage over a killed run's junit fed the test-count drop
    warning and the no-new-failures check last run's numbers."""
    (tmp_path / "junit.xml").write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="s" tests="1">'
        '<testcase classname="t" name="one"/></testsuite></testsuites>',
        encoding="utf-8")
    script = tmp_path / "write.py"
    script.write_text(f"open('cov.json', 'w', encoding='utf-8').write({ISTANBUL!r})\n",
                      encoding="utf-8")
    lane = Lane(name="py", command=f'"{sys.executable}" "{script}"', artifact="cov.json",
                parser="istanbul", scopes=(), results_artifact="junit.xml")

    with pytest.raises(ToolError, match="predates") as raised:
        run_lane(tmp_path, lane)

    assert "junit.xml" in str(raised.value)


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
        _raise_no_artifact(tmp_path, _py_lane(), log, 2)
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


def test_a_reused_artifact_that_is_gone_names_the_log_too(tmp_path):
    """`--reuse-artifacts` after somebody cleaned .crapkit/cov/ raised the one
    refusal that named no log, while the previous run's lane-py.log sat there
    holding the story. docs/lanes.md says every lane refusal carries the path."""
    log = tmp_path / ".crapkit" / "lane-py.log"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(["$ python -m pytest --cov",
                              "E   ImportError: no module named 'faro'"]),
                   encoding="utf-8")

    with pytest.raises(ToolError) as raised:
        run_lane(tmp_path, _py_lane(), reuse_artifact=True)

    assert str(log) in str(raised.value)
    assert "no module named 'faro'" in str(raised.value), "and what the log says"


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

    reason = "cannot import name 'Widget' from '/other/checkout/src'"
    assert reason in message
    assert "short test summary info" not in message, "the budget went to the reason"
    assert "\n...\n" in message, (
        "the two excerpts are not adjacent output, and the ellipsis says so")
    assert message.index(reason) < message.index("ERROR tests/test_0")


def test_a_cause_line_too_long_to_show_whole_keeps_its_end(tmp_path):
    """What identifies an ImportError is the path at the END of the line: in the
    report this came from, the other checkout's. A cut taken from the left drops
    exactly that half and says nothing about having cut."""
    reason = ("E   ImportError: cannot import name 'WidgetRegistry' from partially "
              "initialized module 'faro.core.widgets' (/Users/dev/src/monorepo-checkouts/"
              "project-beta/.venv/lib/python3.11/site-packages/faro/core/widgets/impl.py)")
    assert len(reason) > 200, "the case is a line the cause budget cannot show whole"
    lines = ["$ python -m pytest --cov", reason,
             "=========================== short test summary info ===================="]
    lines += [f"ERROR tests/test_{i:03d}.py" for i in range(60)]
    lines += ["(exit 2)"]

    message = _refusal(tmp_path, lines)

    assert "widgets/impl.py)" in message, "the checkout the line names is the point of it"
    assert "..." in message, "and the reader is told the line was cut"


def test_a_tail_that_already_says_why_is_not_repeated(tmp_path):
    lines = ["$ python -m pytest --cov", "E   ImportError: no module named 'faro'", "(exit 2)"]

    message = _refusal(tmp_path, lines)

    assert message.count("no module named 'faro'") == 1


_NO_COV = ["$ python -m pytest --cov",
           "pytest: error: unrecognized arguments: --cov --cov-branch", "(exit 4)"]


def test_the_pytest_cov_hint_still_fires_through_the_new_tail(tmp_path):
    assert "pytest-cov" in _refusal(tmp_path, _NO_COV)


def test_the_pytest_cov_hint_names_the_interpreter_the_lane_runs(tmp_path):
    """`pip install pytest-cov` named no environment at all, and the package has
    to land in the one the LANE names. A repo whose lane runs its own venv got a
    hint that resolves to whatever venv the shell has active, where installing
    it changes nothing: the reporter ran the line verbatim and the next
    `crapkit coverage` failed identically."""
    from crapkit.lanes import _raise_no_artifact

    lane = Lane(name="py", command='".venv/Scripts/python.exe" -m pytest --cov',
                artifact=".crapkit/cov/py.json", parser="coveragepy", scopes=("src",))
    log = _plant_log(tmp_path, _NO_COV)
    with pytest.raises(ToolError) as raised:
        _raise_no_artifact(tmp_path, lane, log, 4)

    message = str(raised.value)
    assert ".venv/Scripts/python.exe" in message, "the interpreter the lane starts with"
    assert "shell" in message, "and that the shell's active venv is the wrong place"


def test_a_quoted_interpreter_path_reaches_the_hint_as_one_word(tmp_path):
    """Read with the shell that runs the command, never str.split: a quoted
    path with a space in it is one word, and half of it is not an interpreter."""
    from crapkit.lanes import _raise_no_artifact

    lane = Lane(name="py", command='"C:/Program Files/py/python.exe" -m pytest --cov',
                artifact=".crapkit/cov/py.json", parser="coveragepy", scopes=("src",))
    log = _plant_log(tmp_path, _NO_COV)
    with pytest.raises(ToolError) as raised:
        _raise_no_artifact(tmp_path, lane, log, 4)

    assert "C:/Program Files/py/python.exe" in str(raised.value)


def test_one_line_longer_than_the_budget_says_it_was_cut(tmp_path):
    message = _refusal(tmp_path, ["y" * 4000])

    assert "; last output: ..." in message, "an ellipsis marks the one cut a tail cannot avoid"


def test_a_refusal_with_no_log_at_all_still_names_where_it_would_be(tmp_path):
    """Nothing writes the log before the command runs, and a lane refused ahead
    of that has no tail to quote. The path is the half that always applies."""
    from crapkit.lanes import _raise_no_artifact

    missing = tmp_path / ".crapkit" / "lane-py.log"
    with pytest.raises(ToolError) as raised:
        _raise_no_artifact(tmp_path, _py_lane(), missing, None)

    assert str(missing) in str(raised.value)
    assert "last output" not in str(raised.value), "no log, nothing to quote"


# --- a retried lane's log holds every attempt, and the cause is the last one's -
#
# `retries` appends attempt 2 to attempt 1's log under an `--- attempt N ---`
# banner. A cause scan over the whole file hoists whatever attempt died first,
# in front of the tail the LAST attempt wrote, with nothing saying the two came
# from different runs.

def _retried_log(first: list[str], second: list[str]) -> list[str]:
    """Two attempts in one log, the second closing on a summary block wide
    enough that the byte tail never reaches back to its own cause."""
    return (["$ python -m pytest --cov", *first, "(exit 2)", "",
             "--- attempt 2 ---", "$ python -m pytest --cov", *second,
             "=========================== short test summary info ===================="]
            + [f"ERROR tests/test_{i:03d}.py" for i in range(60)]
            + ["(exit 2)"])


def test_the_hoisted_cause_comes_from_the_last_attempt(tmp_path):
    lines = _retried_log(["E   ImportError: no module named 'faro'"],
                         ["E   AttributeError: module 'faro' has no attribute 'Widget'"])

    message = _refusal(tmp_path, lines)

    assert "no attribute 'Widget'" in message, "attempt 2 is the run that failed the lane"
    assert "no module named 'faro'" not in message, (
        "attempt 1 was superseded, and the message marks no attempt boundary")


def test_a_cause_only_the_superseded_attempt_had_is_not_hoisted(tmp_path):
    """Attempt 2 wrote no line naming a reason. The message says that much,
    rather than borrowing the reason attempt 1 gave for dying."""
    lines = _retried_log(["E   ImportError: no module named 'faro'"],
                         ["collected 0 items"])

    message = _refusal(tmp_path, lines)

    assert "no module named 'faro'" not in message


def test_a_log_with_no_banner_is_one_attempt_and_still_hoists(tmp_path):
    """Attempt 1 writes no banner, so a bannerless log is a single attempt and
    the scan covers all of it, exactly as it did before retries were involved."""
    lines = ["$ python -m pytest --cov",
             "E   ImportError: no module named 'faro'",
             "=========================== short test summary info ===================="]
    lines += [f"ERROR tests/test_{i:03d}.py" for i in range(60)]
    lines += ["(exit 2)"]

    assert "no module named 'faro'" in _refusal(tmp_path, lines)


def test_a_line_that_merely_quotes_the_banner_does_not_start_an_attempt(tmp_path):
    """The banner is a whole line. A test asserting on it quotes those same
    words mid-line, and a substring scan would read the cause above such a line
    as superseded and hoist nothing."""
    lines = ["$ python -m pytest --cov",
             "tests/test_lane_log.py:12: in test_the_retry_is_visible",
             "E   AssertionError: assert '--- attempt 2 ---' in log",
             "=========================== short test summary info ===================="]
    lines += [f"ERROR tests/test_{i:03d}.py" for i in range(60)]
    lines += ["(exit 2)"]

    assert "assert '--- attempt 2 ---' in log" in _refusal(tmp_path, lines)
