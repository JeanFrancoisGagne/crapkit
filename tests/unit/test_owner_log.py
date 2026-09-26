"""The measurement owner's stderr goes to owner.log, and every exit-5 line names it.

The owner is a guardian process started with its stderr on DEVNULL, so when it
raised, the traceback went nowhere and the caller printed `measurement owner
stopped before confirming ownership` with no cause. One process on a Linux
host whose name was not UTF-8 did that to every lane run, and nothing on
screen or on disk said why. Its stderr now goes to `.crapkit/owner.log` of
the checkout whose lock it holds, and the line names that file and says
whether this owner wrote to it.
"""
import os
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from crapkit import _process_owner as owner_module
from crapkit.errors import ToolError
from crapkit.procs import own_processes
from hang_guard import exited

# A lock name holding a NUL byte, which no OS can open: the guardian raises
# ValueError before it replies, the way any error it did not expect ends it.
# crapkit never names such a lock, so this crash stays a crash. (An inherited
# family list it cannot parse used to be the trigger; that is now a refusal
# that names the variable, in test_071_family_adapters.)
BROKEN_LOCK = "measurement" + chr(0) + ".lock"


@pytest.mark.parametrize("locks, expected", [
    pytest.param(["repo/.crapkit/measurement.lock"], "repo/.crapkit/owner.log", id="the-measurement-lock"),
    pytest.param(["repo/.crapkit/mutate-leases/0a1b.lock"], "repo/.crapkit/owner.log", id="a-nested-lease"),
    pytest.param(["home/.cache/crapkit/measurements/h/measurement-1.lock", "repo/.crapkit/measurement.lock"],
                 "repo/.crapkit/owner.log", id="an-output-lock-sorted-first"),
    pytest.param([".crapkit/outer/repo/.crapkit/measurement.lock"], ".crapkit/outer/repo/.crapkit/owner.log",
                 id="the-nearest-state-directory"),
])
def test_the_log_sits_in_the_state_directory_of_the_checkout_the_owner_guards(tmp_path, locks, expected):
    assert owner_module._log_path([tmp_path / lock for lock in locks]) == tmp_path / expected


def test_an_owner_holding_no_checkout_lock_logs_beside_the_measurement_locks(tmp_path, monkeypatch):
    """A scoped test run, the MCP server and a probe start an owner with no
    lock at all, and the measurement locks already live in the per-user cache."""
    monkeypatch.setattr(owner_module.Path, "home", classmethod(lambda cls: tmp_path))

    assert owner_module._log_path([]) == tmp_path / ".cache" / "crapkit" / "owner.log"


def _crash(tmp_path, monkeypatch) -> str:
    """Start an owner that raises before it replies, and return the exit-5 line."""
    monkeypatch.delenv("CRAPKIT_COMMAND_FAMILIES", raising=False)
    with pytest.raises(ToolError, match="before confirming ownership") as raised:
        with own_processes([tmp_path / ".crapkit" / BROKEN_LOCK]):
            pass
    return str(raised.value)


def test_a_crashed_owner_leaves_its_traceback_and_the_line_names_the_file(tmp_path, monkeypatch):
    line = _crash(tmp_path, monkeypatch)

    log = tmp_path / ".crapkit" / "owner.log"
    assert line == f"measurement owner stopped before confirming ownership; its error is at the end of {log}"
    text = log.read_text(encoding="utf-8", errors="replace")
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ measurement owner \d+ stopped:\n", text), text
    assert "Traceback" in text and "ValueError: embedded null" in text, text


def test_a_second_crash_appends_to_the_first(tmp_path, monkeypatch):
    """Two owners of two commands can stop on one checkout; the second must
    not erase the first one's cause."""
    _crash(tmp_path, monkeypatch)
    second = _crash(tmp_path, monkeypatch)

    text = (tmp_path / ".crapkit" / "owner.log").read_text(encoding="utf-8", errors="replace")
    assert text.count("Traceback") == 2 and "its error is at the end of" in second, text


def test_a_killed_owner_is_said_to_have_written_nothing(tmp_path):
    """SIGKILL or the OOM killer leaves no traceback, and a log that already
    holds an older one must not be read as this owner's cause."""
    log = tmp_path / ".crapkit" / "owner.log"
    log.parent.mkdir()
    log.write_text("an older owner's traceback\n", encoding="utf-8")

    with pytest.raises(ToolError, match="before publication") as raised:
        with own_processes([tmp_path / ".crapkit" / "measurement.lock"]) as owner:
            owner.process.kill()
            exited(owner.process)

    assert str(raised.value) == f"measurement owner stopped before publication; it wrote nothing to {log}"


class _GonePipe:
    """The guardian's input once the guardian is gone."""

    def write(self, text):
        raise BrokenPipeError(32, "Broken pipe")


def test_an_owner_gone_during_registration_names_the_log(tmp_path):
    log = owner_module._OwnerLog([tmp_path / ".crapkit" / "measurement.lock"])
    log.close()
    gone = owner_module._GuardianOwner(SimpleNamespace(stdin=_GonePipe()), log)

    with pytest.raises(ToolError) as raised:
        gone.stop(os.getpid())

    assert str(raised.value) == ("measurement owner stopped during command registration; "
                                 f"it wrote nothing to {tmp_path / '.crapkit' / 'owner.log'}")


@pytest.mark.parametrize("failure", ["a-file-where-the-directory-goes", "no-home-directory"])
def test_an_owner_that_cannot_open_its_log_still_guards(tmp_path, monkeypatch, failure):
    """A read-only checkout, `.crapkit` taken by a file, or a process with no
    home directory: the owner starts with no log, and the line names none."""
    if failure == "no-home-directory":
        monkeypatch.setattr(owner_module.Path, "home", classmethod(_no_home))
    else:
        (tmp_path / ".crapkit").write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(owner_module, "_log_path", lambda paths: tmp_path / ".crapkit" / "owner.log")

    with pytest.raises(ToolError) as raised:
        with own_processes([tmp_path / "measurement.lock"]) as owner:
            owner.process.kill()
            exited(owner.process)

    assert str(raised.value) == "measurement owner stopped before publication"


def _no_home(cls):
    raise RuntimeError("Could not determine home directory.")


def test_the_lanes_page_quotes_the_line_the_owner_prints():
    page = (Path(__file__).resolve().parents[2] / "docs" / "lanes.md").read_text(encoding="utf-8")

    assert ("crapkit: measurement owner stopped before confirming ownership; "
            "its error is at the end of /repo/.crapkit/owner.log") in page
