"""procs reaches its command owner through one public surface, in one direction.

The owner module is also the guardian script, so it must start without the
spawning module. procs hands the owner back an opaque registration and never
builds or reads the owner's request payload.
"""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

from crapkit import _process_owner as owner_module
from crapkit import procs


class _PublicOwner:
    """Exactly the owner methods procs may call, and nothing else."""

    def __init__(self):
        self.registration = object()
        self.events = []

    def prepare(self, popen_kwargs):
        return self.registration, popen_kwargs

    def register_then(self, pid, release, registration=None):
        self.events.append(("register", pid, registration))
        release()

    def stop(self, pid):
        self.events.append(("stop", pid))

    def check_cancelled(self):
        self.events.append(("check_cancelled",))


def test_the_owner_module_loads_without_procs():
    probe = "import sys, crapkit._process_owner; print('crapkit.procs' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                            encoding="utf-8", check=True)
    assert result.stdout.strip() == "False"


def _events_for_one_command(owner):
    (register, pid, registration), stop, checked = owner.events
    assert register == "register"
    assert registration is owner.registration
    assert stop == ("stop", pid)
    assert checked == ("check_cancelled",)


def test_run_owned_passes_the_owners_registration_back_unread():
    owner = _PublicOwner()
    result = procs.run_owned([sys.executable, "-c", "print('ran')"], owner=owner,
                             capture_output=True)
    assert (result.returncode, result.stdout) == (0, "ran\n")
    _events_for_one_command(owner)


def test_run_bounded_passes_the_owners_registration_back_unread(tmp_path):
    owner = _PublicOwner()
    log = tmp_path / "command.log"
    with log.open("wb") as stream:
        code = procs.run_bounded(f'"{sys.executable}" -c "print(7)"', 10, stream=stream,
                                 owner=owner)
    assert code == 0
    assert log.read_bytes().strip() == b"7"
    _events_for_one_command(owner)


def test_windows_commands_need_no_family(monkeypatch):
    kwargs = {"cwd": "here", "env": {"ONLY": "value"}}
    monkeypatch.setattr(owner_module, "os", SimpleNamespace(name="nt"))
    assert owner_module.command_registration(kwargs) == (None, kwargs)


def test_posix_commands_carry_their_family_in_the_environment(monkeypatch):
    monkeypatch.setenv("CRAPKIT_COMMAND_FAMILIES", "[]")
    monkeypatch.setattr(owner_module, "os", SimpleNamespace(name="posix"))
    family, kwargs = owner_module.command_registration({"cwd": "here", "env": {"ONLY": "value"}})
    assert kwargs["cwd"] == "here"
    assert kwargs["env"]["ONLY"] == "value"
    assert json.loads(kwargs["env"]["CRAPKIT_COMMAND_FAMILIES"]) == [family]
    assert not os.path.exists(family), "naming a family before spawn must not create it"


def test_every_owner_prepares_commands_the_same_way(monkeypatch):
    monkeypatch.setattr(owner_module, "command_registration", lambda kwargs: ("named", kwargs))
    with procs.own_processes(()) as owner:
        assert owner.prepare({"cwd": "here"}) == ("named", {"cwd": "here"})


class _GonePipe:
    """A child's input pipe after the child exited: closing it fails."""

    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1
        raise BrokenPipeError(32, "Broken pipe")


def test_one_input_close_serves_procs_and_the_guardian():
    assert procs.close_input is owner_module.close_input
    owner_module.close_input(SimpleNamespace(stdin=None))  # DEVNULL input: no pipe to close
    pipe = _GonePipe()
    owner_module.close_input(SimpleNamespace(stdin=pipe))
    assert pipe.closes == 1
