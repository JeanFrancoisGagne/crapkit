"""Whether an attempt wrote its declared files, decided by where the files are.

The runner moves a lane's artifact and results file aside under .crapkit/
before each attempt (lane_outputs). A file at a declared path afterwards is
one the attempt wrote; a leftover goes back only where the attempt wrote
nothing. The old test compared modification times, so a command that only
touched the previous report passed, and the previous run's numbers were scored
and stamped as this commit's (stale-touch shape-8, boundary-11). The flake
retest judged its junit the same way and ignored a retest that rewrote it
inside the old file's time tick (boundary-23).

Every lane here writes, touches or leaves its artifact through a real child
process started by the lane runner.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lane_outputs import owned
from crapkit.lanes import retest_lane, run_lane

ISTANBUL = json.dumps({"C:/r/src/a.ts": {"fnMap": {}, "f": {}, "branchMap": {}, "b": {}}})
OLD = 1_000_000_000

SCRIPTS = {
    # the loops' touch: os.utime on the declared path, which is empty now
    "utime-only": "import os, time; t = time.time(); os.utime('cov.json', (t, t))",
    # a shell `touch`: creates an empty file where the old report was
    "create-empty": "import pathlib; pathlib.Path('cov.json').touch()",
    "nothing": "pass",
    "same-bytes": f"open('cov.json', 'w', encoding='utf-8').write({ISTANBUL!r})",
    "new-bytes": f"open('cov.json', 'w', encoding='utf-8').write({ISTANBUL + ' '!r})",
    # boundary-11's same-bytes lane: it reads the old report to write it back
    "reads-its-old-report": "data = open('cov.json').read(); open('cov.json', 'w').write(data)",
}


def _script(root: Path, name: str, body: str) -> str:
    """A command that runs `body` from a file, so no quoting reaches the shell."""
    (root / name).write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{root / name}"'


def _lane(root: Path, script: str) -> Lane:
    return Lane(name="py", command=_script(root, "attempt.py", script), artifact="cov.json",
                parser="istanbul", scopes=())


def _previous_run(root: Path) -> None:
    path = root / "cov.json"
    path.write_text(ISTANBUL, encoding="utf-8")
    os.utime(path, ns=(OLD, OLD))


# (what the attempt did, the words its refusal holds or "" for a scored run)
ATTEMPTS = {
    "utime-only": "wrote no artifact this run",
    "create-empty": "",
    "nothing": "wrote no artifact this run",
    "same-bytes": "",
    "new-bytes": "",
    "reads-its-old-report": "wrote no artifact this run",
}


@pytest.mark.parametrize("name", sorted(ATTEMPTS))
def test_only_a_file_the_attempt_wrote_is_scored(name, tmp_path):
    _previous_run(tmp_path)
    lane = _lane(tmp_path, SCRIPTS[name])

    if ATTEMPTS[name]:
        with pytest.raises(ToolError, match=ATTEMPTS[name]) as raised:
            run_lane(tmp_path, lane)
        assert raised.value.refused == {"cov.json": _sha(tmp_path / "cov.json")}
        assert (tmp_path / "cov.json").read_text(encoding="utf-8") == ISTANBUL
        assert (tmp_path / "cov.json").stat().st_mtime_ns == OLD, "the leftover went back as it was"
        return
    if name == "create-empty":
        with pytest.raises(ToolError) as raised:
            run_lane(tmp_path, lane)
        assert "wrote no artifact" not in str(raised.value), "the attempt wrote this empty file"
        assert (tmp_path / "cov.json").read_bytes() == b"", "never the previous run's numbers"
        return
    assert run_lane(tmp_path, lane).provenance["exit_code"] == 0


def test_the_aside_directory_is_gone_after_the_run(tmp_path):
    _previous_run(tmp_path)

    with pytest.raises(ToolError):
        run_lane(tmp_path, _lane(tmp_path, SCRIPTS["nothing"]))

    assert not (tmp_path / ".crapkit" / "aside").exists()


def _sha(path: Path) -> str:
    from crapkit.lane_stamps import file_sha256

    return file_sha256(path)


# --- the Outputs seam itself ------------------------------------------------------

def test_a_partial_file_an_earlier_attempt_left_is_cleared_before_the_next(tmp_path):
    _previous_run(tmp_path)
    with owned(tmp_path, "py", ("cov.json",)) as outputs:
        (tmp_path / "cov.json").write_text("{partial", encoding="utf-8")
        outputs.clear()
        assert not outputs.written("cov.json")
        assert outputs.unwritten() == ["cov.json"]

    assert (tmp_path / "cov.json").read_text(encoding="utf-8") == ISTANBUL
    assert outputs.leftovers == {"cov.json": _sha(tmp_path / "cov.json")}


def test_a_crash_inside_the_attempt_puts_the_leftover_back(tmp_path):
    _previous_run(tmp_path)

    with pytest.raises(KeyboardInterrupt):
        with owned(tmp_path, "py", ("cov.json",)):
            assert not (tmp_path / "cov.json").exists()
            raise KeyboardInterrupt

    assert (tmp_path / "cov.json").read_text(encoding="utf-8") == ISTANBUL


def test_a_file_the_attempt_wrote_replaces_the_leftover(tmp_path):
    _previous_run(tmp_path)
    with owned(tmp_path, "py", ("cov.json",)) as outputs:
        (tmp_path / "cov.json").write_text("new", encoding="utf-8")

    assert (tmp_path / "cov.json").read_text(encoding="utf-8") == "new"
    assert outputs.leftovers == {}


def test_a_file_that_cannot_be_moved_aside_is_named(tmp_path, monkeypatch):
    import crapkit.lane_outputs as lane_outputs

    _previous_run(tmp_path)

    def refuses(*_args):
        raise PermissionError("in use")

    monkeypatch.setattr(lane_outputs.shutil, "move", refuses)
    with pytest.raises(ToolError, match=r"cannot move cov.json aside .* \(in use\)"):
        with owned(tmp_path, "py", ("cov.json",)):
            pass


# --- the flake retest -------------------------------------------------------------

PASSING = ('<?xml version="1.0"?><testsuites><testsuite name="s" tests="1">'
           '<testcase classname="tests" name="t1"/></testsuite></testsuites>')
FAILING = ('<?xml version="1.0"?><testsuites><testsuite name="s" tests="1">'
           '<testcase classname="tests" name="t1"><failure/></testcase></testsuite></testsuites>')

RETESTS = {
    # what the retest command does to junit.xml, and whether t1 counts as a flake
    "rewrites-passing": (f"open('junit.xml', 'w').write({PASSING!r})", True),
    "rewrites-passing-old-time": (
        f"import os; open('junit.xml', 'w').write({PASSING!r}); os.utime('junit.xml', ns=({OLD}, {OLD}))",
        True),
    "utime-only": ("import os, time; t = time.time(); os.utime('junit.xml', (t, t))", False),
    "writes-nothing": ("pass", False),
}


@pytest.mark.parametrize("name", sorted(RETESTS))
def test_the_retest_counts_the_report_it_wrote_and_only_that_one(name, tmp_path):
    """rewrites-passing-old-time is boundary-23's same-size pass: the retest's
    report kept the failing one's time, and its passes were ignored."""
    (tmp_path / "junit.xml").write_text(FAILING, encoding="utf-8")
    os.utime(tmp_path / "junit.xml", ns=(OLD, OLD))
    script, flake = RETESTS[name]
    lane = Lane(name="py", command="unused", artifact="cov.json", parser="istanbul", scopes=(),
                results_artifact="junit.xml",
                retest_command=_script(tmp_path, "retest.py", script) + " {tests}")

    passed = retest_lane(tmp_path, lane, {"tests::t1"})

    assert passed == ({"tests::t1"} if flake else set())
    if not flake:
        assert (tmp_path / "junit.xml").read_text(encoding="utf-8") == FAILING, "the lane's report"


# --- an attempt that never finished -------------------------------------------------
#
# A kill (taskkill /F, SIGKILL, a CI job timeout) runs no cleanup, so the files
# an attempt set aside stay under .crapkit/aside/. `--reuse-artifacts` then found
# no artifact and exited 5 without naming the copy, and the next attempt's exit
# removed the aside directory with the previous artifact in it. Whoever next
# holds the lane's measurement lock puts the copy back where nothing was
# written since, and names it where something was.

KILLED = """import os, sys, time
sys.path.insert(0, {src!r})
from pathlib import Path
from crapkit.lane_outputs import owned
owned(Path({root!r}), {owner!r}, ({name!r},)).__enter__()
{write}
print("aside", flush=True)
time.sleep({hold})
"""


def _killed_attempt(root: Path, owner: str, name: str, write: str = "") -> None:
    """A child that sets `name` aside as `owner`'s attempt, is killed, and never
    runs its exit."""
    import subprocess

    import crapkit
    from hang_guard import CHILD_HOLD, exited, next_line

    script = KILLED.format(src=str(Path(crapkit.__file__).parents[1]), root=str(root),
                           owner=owner, name=name, write=write, hold=CHILD_HOLD)
    child = subprocess.Popen([sys.executable, "-c", script], cwd=root, stdout=subprocess.PIPE,
                             text=True)
    assert next_line(child).strip() == "aside"
    child.kill()
    exited(child)
    child.stdout.close()


def _reused(root: Path, lane: Lane):
    return run_lane(root, lane, reuse_artifact=True)


# (the lane's command, what the lane run after the kill must leave at cov.json)
AFTER_KILL = {
    "reuse": (None, ISTANBUL),
    "run-writes-nothing": (SCRIPTS["nothing"], ISTANBUL),
    "run-writes-new-bytes": (SCRIPTS["new-bytes"], ISTANBUL + " "),
}


@pytest.mark.parametrize("next_command", sorted(AFTER_KILL))
def test_a_killed_attempt_s_leftover_is_back_for_the_next_command(next_command, tmp_path, capsys):
    _previous_run(tmp_path)
    _killed_attempt(tmp_path, "py", "cov.json")
    assert not (tmp_path / "cov.json").exists(), "the kill left the artifact aside"
    script, kept = AFTER_KILL[next_command]

    if script is None:
        assert _reused(tmp_path, _lane(tmp_path, SCRIPTS["nothing"])).provenance["exit_code"] is None
    elif kept == ISTANBUL:
        with pytest.raises(ToolError, match="wrote no artifact this run") as raised:
            run_lane(tmp_path, _lane(tmp_path, script))
        assert raised.value.refused == {"cov.json": _sha(tmp_path / "cov.json")}
    else:
        run_lane(tmp_path, _lane(tmp_path, script))

    assert (tmp_path / "cov.json").read_text(encoding="utf-8") == kept
    assert "lane 'py': cov.json is back at its path" in capsys.readouterr().err
    assert not (tmp_path / ".crapkit" / "aside").exists()


def test_a_killed_retest_s_report_is_back_for_the_next_command(tmp_path, capsys):
    (tmp_path / "cov.json").write_text(ISTANBUL, encoding="utf-8")
    (tmp_path / "junit.xml").write_text(FAILING, encoding="utf-8")
    _killed_attempt(tmp_path, "py retest", "junit.xml")
    lane = Lane(name="py", command="unused", artifact="cov.json", parser="istanbul", scopes=(),
                results_artifact="junit.xml")

    _reused(tmp_path, lane)

    assert (tmp_path / "junit.xml").read_text(encoding="utf-8") == FAILING
    assert "lane 'py': junit.xml is back at its path" in capsys.readouterr().err


def test_a_file_the_killed_attempt_wrote_stays_and_the_copy_it_set_aside_is_named(tmp_path, capsys):
    """A file at the declared path may be one written after the kill, by hand or
    by the test command run alone, so crapkit never writes over it."""
    _previous_run(tmp_path)
    _killed_attempt(tmp_path, "py", "cov.json",
                    write="open('cov.json', 'w', encoding='utf-8').write('{\"partial')")

    with pytest.raises(ToolError):
        _reused(tmp_path, _lane(tmp_path, SCRIPTS["nothing"]))

    err = capsys.readouterr().err
    copy = next((tmp_path / ".crapkit" / "aside").glob("py-*/0-cov.json"))
    assert copy.read_text(encoding="utf-8") == ISTANBUL
    assert (tmp_path / "cov.json").read_text(encoding="utf-8") == '{"partial'
    assert f"the previous one is at {copy.relative_to(tmp_path).as_posix()}" in err
    assert "move it back to cov.json to reuse it, or rerun the lane" in err
