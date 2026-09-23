"""A test-side wait on a child returns when its state appears and fails a hung
child with what it printed.

These tests never measure wall-clock time. A miss is forced by a child that has
already exited, or by setting the bound to zero, so each one runs in the time a
child takes to start.
"""
import os
import signal
import subprocess
import sys
import textwrap

import pytest

import hang_guard
from hang_guard import (CHAINED_WAITS, CHILD_HOLD, CHILD_WAIT, HANG_ENV, HANG_SECONDS,
                        HOLD_SECONDS, communicate, exited, next_line, run, wait_for,
                        wait_until)

TALKER = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    print("reached step one", flush=True)
    print("stuck on step two", file=sys.stderr, flush=True)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text("printed")
        time.sleep(600)
    raise SystemExit(3)
""")


# -S: these children need only the standard library, and skipping site packages
# keeps each start under a second.
PYTHON = [sys.executable, "-S"]


def talker(*args, **popen):
    popen = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True, **popen}
    return subprocess.Popen([*PYTHON, "-c", TALKER, *map(str, args)], **popen)


@pytest.fixture
def run_out(monkeypatch):
    """Call it to make the bound run out: every wait after the call misses at once."""
    return lambda: monkeypatch.setattr(hang_guard, "HANG_SECONDS", 0)


def hung_talker(tmp_path):
    """A child that has printed both lines and now sleeps."""
    printed = tmp_path / "printed"
    child = talker(printed)
    wait_for(printed, child)
    return child


def test_a_wait_returns_on_the_first_check_that_sees_its_state():
    checks = iter([False, False, True])
    seen = []

    def condition():
        seen.append(next(checks))
        return seen[-1]

    wait_until(condition)

    assert seen == [False, False, True]


def test_a_child_that_exits_before_the_state_is_a_miss_that_shows_its_output(tmp_path):
    child = talker()

    with pytest.raises(AssertionError) as missed:
        wait_for(tmp_path / "never", child)

    report = str(missed.value)
    assert "exited with code 3" in report
    assert "reached step one" in report and "stuck on step two" in report


def test_a_state_that_appears_as_the_child_exits_is_not_a_miss(tmp_path):
    marker = tmp_path / "written-then-exited"
    child = subprocess.Popen([*PYTHON, "-c",
                              f"from pathlib import Path; Path({str(marker)!r}).touch()"])
    exited(child)

    wait_for(marker, child)


def test_a_bound_that_runs_out_kills_the_child_and_shows_what_it_printed(tmp_path, run_out):
    child = hung_talker(tmp_path)
    run_out()

    with pytest.raises(AssertionError) as missed:
        wait_for(tmp_path / "never", child)

    assert child.poll() is not None, "the hung child must be killed"
    report = str(missed.value)
    assert "was killed" in report
    assert "reached step one" in report and "stuck on step two" in report


HOLDER = textwrap.dedent("""
    import subprocess, sys, time
    from pathlib import Path
    grandchild = subprocess.Popen([sys.executable, "-S", "-c", "import time; time.sleep(600)"],
                                  stdout=sys.stdout, stderr=sys.stderr)
    marker = Path(sys.argv[1])
    partial = marker.with_suffix(".part")
    partial.write_text(str(grandchild.pid))
    partial.replace(marker)  # the marker exists only once it holds the pid
    time.sleep(600)
""")


def test_a_descendant_holding_the_pipes_cannot_stall_the_report(tmp_path, run_out, monkeypatch):
    marker = tmp_path / "grandchild-pid"
    child = subprocess.Popen([*PYTHON, "-c", HOLDER, str(marker)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for(marker, child)
        run_out()
        monkeypatch.setattr(hang_guard, "_REPORT_SECONDS", .5)
        with pytest.raises(AssertionError, match="a descendant still holds the child's pipes"):
            wait_for(tmp_path / "never", child)
    finally:
        os.kill(int(marker.read_text()), signal.SIGTERM)
        child.communicate()


def test_on_miss_frees_the_pipes_before_a_missed_communicate_reads_them(tmp_path, run_out):
    marker = tmp_path / "grandchild-pid"
    child = subprocess.Popen([*PYTHON, "-c", HOLDER, str(marker)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    released = []

    def release():
        os.kill(int(marker.read_text()), signal.SIGTERM)
        released.append(marker)

    try:
        wait_for(marker, child)
        run_out()
        with pytest.raises(AssertionError) as missed:
            communicate(child, on_miss=release)
        assert str(missed.value).endswith("--- the child printed ---\n(nothing)")
    finally:
        if not released:
            release()
        child.kill()
        child.communicate()


def test_a_miss_whose_log_was_never_written_still_reports_the_miss(tmp_path, run_out):
    absent = tmp_path / "no-such.log"
    run_out()

    with pytest.raises(AssertionError) as missed:
        wait_for(tmp_path / "never", log=absent)

    assert str(missed.value).endswith(f"--- the child printed ---\n(no log at {absent})")


def test_a_miss_without_a_child_names_what_it_waited_for(tmp_path, run_out):
    run_out()
    with pytest.raises(AssertionError, match="never saw .*absent-marker"):
        wait_for(tmp_path / "absent-marker")


def test_a_child_writing_to_a_log_is_reported_from_the_log(tmp_path):
    log = tmp_path / "child.log"
    with log.open("w") as stream:
        child = talker(stdout=stream, stderr=stream)
        with pytest.raises(AssertionError) as missed:
            wait_for(tmp_path / "never", child, log=log)

    assert "reached step one" in str(missed.value)


def test_a_child_with_no_captured_output_reports_only_how_the_wait_ended(tmp_path):
    child = talker(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    with pytest.raises(AssertionError) as missed:
        wait_for(tmp_path / "never", child)

    assert "exited with code 3" in str(missed.value)
    assert "(nothing captured)" in str(missed.value)


def test_exited_returns_the_exit_code():
    assert exited(talker(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)) == 3


def test_exited_kills_a_hung_child_and_reports_it(tmp_path, run_out):
    child = hung_talker(tmp_path)
    run_out()

    with pytest.raises(AssertionError, match="never saw the child exit") as missed:
        exited(child)

    assert child.poll() is not None
    assert "stuck on step two" in str(missed.value)


def test_communicate_returns_both_streams():
    assert communicate(talker()) == ("reached step one\n", "stuck on step two\n")


def test_communicate_kills_a_hung_child_and_reports_its_output(tmp_path, run_out):
    child = hung_talker(tmp_path)
    run_out()

    with pytest.raises(AssertionError) as missed:
        communicate(child)

    assert child.poll() is not None
    assert "reached step one" in str(missed.value)


def test_run_captures_a_finished_child_like_subprocess_run():
    done = run([*PYTHON, "-c", "import sys; print(sys.stdin.read().upper())"],
               input="quiet", text=True)

    assert (done.returncode, done.stdout, done.stderr) == (0, "QUIET\n", "")


def test_run_kills_a_child_that_outlives_the_bound(run_out):
    run_out()
    with pytest.raises(AssertionError, match="was killed"):
        run([*PYTHON, "-c", "import time; time.sleep(600)"])


def test_run_kills_a_child_that_outlives_a_bound_the_caller_names():
    with pytest.raises(AssertionError, match="within 0 s, so the child was killed"):
        run([*PYTHON, "-c", "import time; time.sleep(600)"], timeout=0)


def test_next_line_reads_the_first_line_the_child_prints():
    assert next_line(talker()) == "reached step one\n"


def test_next_line_kills_a_child_that_prints_nothing(run_out):
    run_out()
    child = subprocess.Popen([*PYTHON, "-c", "import time; time.sleep(600)"],
                             stdout=subprocess.PIPE, text=True)

    with pytest.raises(AssertionError, match="never saw a line") as missed:
        next_line(child)

    assert child.poll() is not None
    assert str(missed.value).endswith("--- the child printed ---\n(nothing)")


def test_a_child_script_reads_the_bound_and_the_hold_from_the_environment():
    code = f"import os; print({CHILD_WAIT}, {CHILD_HOLD})"
    done = run([*PYTHON, "-c", code], text=True)

    assert os.environ[HANG_ENV] == str(HANG_SECONDS)
    assert done.stdout.split() == [str(float(HANG_SECONDS)), str(float(HOLD_SECONDS))]


def test_a_hold_outlasts_every_chain_of_waits_that_starts_after_it():
    """Each wait in a chain may spend the whole bound. A hold that ran out before
    the last one would release its lock, and that wait would pass a product that
    never stopped the holder."""
    assert HOLD_SECONDS > CHAINED_WAITS * HANG_SECONDS
