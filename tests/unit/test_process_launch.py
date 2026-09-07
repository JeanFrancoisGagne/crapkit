"""Helper provenance and launch errors stay separate from command results."""
import os
import subprocess
import sys

import pytest

from crapkit import procs
from crapkit.errors import ToolError


def test_owner_uses_running_package_while_command_keeps_its_environment(tmp_path, monkeypatch):
    package = tmp_path / "crapkit"
    package.mkdir()
    (package / "__init__.py").write_text("origin = 'target package'\n", encoding="utf-8")
    (package / "_process_owner.py").write_text("raise RuntimeError('wrong owner')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    script = tmp_path / "target.py"
    script.write_text("import crapkit; print(crapkit.origin)\n", encoding="utf-8")
    log = tmp_path / "command.log"
    with log.open("wb") as output:
        assert procs.run_bounded(f'"{sys.executable}" "{script}"', 10, stream=output) == 0
    assert log.read_text(encoding="utf-8").strip() == "target package"


@pytest.mark.parametrize("exit_code", [0, 1, 127, 255])
def test_command_stderr_and_exit_codes_are_never_launcher_errors(tmp_path, exit_code):
    script = tmp_path / "target.py"
    script.write_text("import os, sys\nos.write(2, b'[2, \"not a launch error\", null]')\n"
                      f"sys.exit({exit_code})\n", encoding="utf-8")
    log = tmp_path / "command.log"
    with log.open("wb") as output:
        assert procs.run_bounded(f'"{sys.executable}" "{script}"', 10, stream=output) == exit_code
    assert log.read_bytes() == b'[2, "not a launch error", null]'


@pytest.mark.skipif(os.name != "nt", reason="POSIX uses the fixed /bin/sh launcher")
def test_shell_launch_os_error_reaches_caller_after_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("COMSPEC", str(tmp_path / "missing-shell.exe"))
    with pytest.raises(OSError) as direct:
        subprocess.run("exit 1", shell=True)
    with pytest.raises(type(direct.value)) as owned:
        procs.run_bounded("exit 1", 10)
    assert owned.value.errno == direct.value.errno
    assert owned.value.winerror == direct.value.winerror
    assert owned.value.filename == direct.value.filename


def test_unexpected_launcher_failure_is_a_tool_error(monkeypatch):
    monkeypatch.setattr(procs, "_OWNED_LAUNCH", "import sys; sys.stdin.buffer.readline(); "
                        "raise RuntimeError('launcher startup failed')")
    with pytest.raises(ToolError, match="command launcher failed:"):
        procs.run_bounded("unused", 10)
