"""A lane's timeout has to kill the suite, not just the shell in front of it.

`shell=True` makes the shell the child and the test runner a grandchild.
subprocess.run's timeout killed the shell alone, so `timeout_seconds` - the
consumer's only guard against a hung suite - raised the ToolError on schedule
and left the suite running with nothing waiting on it. The lane log even said
"killed". Nothing was.

So both lane spawns go through procs.run_bounded, which streams into the log
file while still killing the whole process group on the deadline. The log is a
real file: it needs no reader, so streaming costs the deadline nothing.
"""
import json
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import _deadline, _no_progress, run_lane
from crapkit.procs import NoProgress, run_bounded

_TIMEOUT = 2
_IDLE = 1        # the no-progress deadline: shorter, and it starts at the header
_CEILING = 5     # the ToolError has to land between the two
_SLEEP = 30      # long enough that a survivor is unmistakable
_POLL = 3.0

MINIMAL_ART = json.dumps({"C:/r/src/a.ts": {"fnMap": {}, "f": {}, "branchMap": {}, "b": {}}})

_COUNT_PYTHON = ("Get-CimInstance Win32_Process | "
                 "Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*%s*' } "
                 "| Measure-Object | Select-Object -ExpandProperty Count")

_NO_LISTER = shutil.which("powershell" if sys.platform == "win32" else "ps") is None


def _alive(token: str) -> bool:
    """Is a process whose command line carries this token still running? The
    Windows query filters on python.exe, so the PowerShell being asked - whose
    own command line holds the token - is not counted as the answer."""
    if sys.platform == "win32":
        out = subprocess.run(["powershell", "-NoProfile", "-Command", _COUNT_PYTHON % token],
                             capture_output=True, text=True)
        return out.stdout.strip() not in ("", "0")
    out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True)
    return token in out.stdout


def _gone(token: str) -> bool:
    """Nothing is running that token, giving a dying tree a moment to go."""
    deadline = time.time() + _POLL
    while time.time() < deadline:
        if not _alive(token):
            return True
        time.sleep(0.2)
    return not _alive(token)


def _long_sleeper(tmp_path) -> tuple:
    """A lane command that reports it started and then outlives any timeout.

    The script name is unique per test because the question is asked of the
    whole machine: a second checkout running this same suite answers a shared
    command line, and the test would read someone else's sleeper as a leak.
    """
    script = tmp_path / f"lane_orphan_{uuid.uuid4().hex}.py"
    script.write_text("import pathlib, time\n"
                      "pathlib.Path(__file__).with_suffix('.started').touch()\n"
                      f"time.sleep({_SLEEP})\n", encoding="utf-8")
    return f'"{sys.executable}" "{script}"', script.name, script.with_suffix(".started")


@pytest.mark.skipif(_NO_LISTER, reason="no process list to ask on this machine")
def test_a_timed_out_lane_takes_its_whole_process_tree_with_it(tmp_path):
    """The reported bug: `crapkit coverage` gave up on the lane at 2 s, wrote
    "killed" into the log, and the suite ran to the end on a machine the user
    thought was free. timeout_seconds is the only guard there is."""
    command, token, started = _long_sleeper(tmp_path)
    lane = Lane(name="slow", command=command, artifact="cov.json", parser="istanbul",
                scopes=(), timeout_seconds=_TIMEOUT)

    start = time.perf_counter()
    with pytest.raises(ToolError, match="timed out"):
        run_lane(tmp_path, lane)

    assert time.perf_counter() - start < _CEILING
    assert started.is_file(), "the lane command never started: this proved nothing"
    log = (tmp_path / ".crapkit" / "lane-slow.log").read_text(encoding="utf-8")
    assert f"[crapkit] timed out after {_TIMEOUT}s; killed" in log
    assert _gone(token), "the lane command outlived the timeout the log says killed it"


@pytest.mark.skipif(_NO_LISTER, reason="no process list to ask on this machine")
def test_a_lane_that_stops_writing_dies_at_the_progress_deadline(tmp_path):
    """The reported hang: a suite that stops making progress at 0% CPU. With no
    `timeout_seconds` the wait had no deadline at all, so crapkit sat on it
    forever with nothing watching the log. `no_progress_seconds` watches the
    log: it grows while the suite reports, and stops when the suite stops."""
    command, token, started = _long_sleeper(tmp_path)
    lane = Lane(name="stalled", command=command, artifact="cov.json", parser="istanbul",
                scopes=(), no_progress_seconds=_IDLE)

    start = time.perf_counter()
    with pytest.raises(ToolError, match="wrote no output for"):
        run_lane(tmp_path, lane)

    assert time.perf_counter() - start < _CEILING
    assert started.is_file(), "the lane command never started: this proved nothing"
    log = (tmp_path / ".crapkit" / "lane-stalled.log").read_text(encoding="utf-8")
    assert f"[crapkit] no output for {_IDLE}s; killed" in log
    assert _gone(token), "the stalled command outlived the deadline that killed the lane"


def test_run_bounded_says_which_deadline_killed_the_tree(tmp_path):
    """The seam: a total deadline returns None, a stall raises. The caller has
    two different things to write in the log, and one of them says the suite
    was still alive and silent."""
    log = tmp_path / "out.log"

    with open(log, "w", encoding="utf-8") as fh:
        fh.write("started\n")
        fh.flush()
        with pytest.raises(NoProgress, match="no output for"):
            run_bounded(f'"{sys.executable}" -c "import time; time.sleep({_SLEEP})"', None,
                        stream=fh, no_progress=_IDLE, cwd=tmp_path)


def test_a_command_that_keeps_writing_outlives_the_progress_deadline(tmp_path):
    """The deadline measures GROWTH, not wall time: a suite printing a dot per
    test runs as long as it likes. A deadline that fired on elapsed time would
    be `timeout_seconds` under another name."""
    script = tmp_path / "chatty.py"
    script.write_text("import sys, time\n"
                      "for _ in range(8):\n"
                      "    sys.stdout.write('tick\\n'); sys.stdout.flush(); time.sleep(0.25)\n",
                      encoding="utf-8")
    log = tmp_path / "out.log"

    with open(log, "w", encoding="utf-8") as fh:
        code = run_bounded(f'"{sys.executable}" "{script}"', None, stream=fh,
                           no_progress=_IDLE, cwd=tmp_path)

    assert code == 0
    assert log.read_text(encoding="utf-8").count("tick") == 8


def test_a_progress_deadline_with_no_stream_to_watch_never_fires(tmp_path):
    """Output going to DEVNULL leaves nothing to measure, and a watch that
    cannot see growth would read every command as stalled and kill it."""
    code = run_bounded(f'"{sys.executable}" -c "import time; time.sleep(1.5)"', None,
                       no_progress=_IDLE, cwd=tmp_path)

    assert code == 0


def test_a_lane_without_a_progress_deadline_gets_no_watch():
    """0 is the config default: the total deadline is the only one."""
    assert _no_progress(Lane(name="n", command="c", artifact="a", parser="istanbul",
                             scopes=())) is None


_PAYLOAD = "line one\nline two: 42 -> 43\nline three\n"


def _echoing_lane(tmp_path, name: str) -> Lane:
    """A lane that prints a known blob and writes a parseable artifact."""
    script = tmp_path / "fast.py"
    script.write_text("import sys\n"
                      f"sys.stdout.write({_PAYLOAD!r})\n"
                      "open('cov.json', 'w', encoding='utf-8').write("
                      f"{MINIMAL_ART!r})\n", encoding="utf-8")
    return Lane(name=name, command=f'"{sys.executable}" "{script}"',
                artifact="cov.json", parser="istanbul", scopes=())


def test_a_fast_lane_still_streams_its_output_into_the_log(tmp_path):
    """Routing the spawn through run_bounded must not cost the log a byte: the
    file handle is the child's stdout the same way, and a tailed log is how a
    long lane is supervised at all."""
    lane = _echoing_lane(tmp_path, "fast")

    _, provenance, _ = run_lane(tmp_path, lane)

    assert provenance["exit_code"] == 0
    log = (tmp_path / ".crapkit" / "lane-fast.log")
    text = log.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text.startswith(f"$ {lane.command}\n"), "the log still opens with the command"
    assert _PAYLOAD in text, "the command's own output is what the log is for"
    assert text.endswith("\n(exit 0)\n")


def test_a_lane_without_a_timeout_gets_no_deadline_at_all():
    """0 is the config default and means the suite decides when it is done.
    A run_bounded called with 0 would kill the lane the instant it started."""
    assert _deadline(Lane(name="n", command="c", artifact="a", parser="istanbul",
                          scopes=(), timeout_seconds=0)) is None


def test_run_bounded_hands_back_the_exit_code_of_a_streamed_command(tmp_path):
    """The seam itself: a stream target changes where the output goes, nothing
    else. The exit code is what the lane records as provenance."""
    log = tmp_path / "out.log"
    with open(log, "w", encoding="utf-8") as fh:
        code = run_bounded(f'"{sys.executable}" -c "import sys; print(7); sys.exit(3)"',
                           None, stream=fh, cwd=tmp_path)

    assert code == 3
    assert log.read_text(encoding="utf-8").strip() == "7"
