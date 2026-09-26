"""The spelling every next-step and refusal uses for crapkit itself.

README tells a reader working from a source checkout to run `python -m crapkit`,
and the pre-commit hook it documents spells the same form with an absolute
interpreter path because git runs hooks outside the activated venv. Neither of
those environments puts a `crapkit` on PATH. Both used to be answered with "run
`crapkit coverage`", so `init` finished by naming a command the shell it just
ran in exits 127 on.

The process knows how it was started. `sys.argv[0]` is the console script when
that is what launched it and the `__main__.py` inside the package when
`python -m` did, so the message can name the form that resolves.
"""
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import invocation
from crapkit.errors import CrapkitError
from crapkit.invocation import _self, shell_path

MODULE_RUN = str(Path(sys.prefix) / "Lib" / "site-packages" / "crapkit" / "__main__.py")
CONSOLE_RUN = str(Path(sys.prefix) / "Scripts" / "crapkit.exe")


@pytest.fixture()
def as_module(monkeypatch):
    """argv as `python -m crapkit` leaves it: the package's own __main__.py."""
    monkeypatch.setattr(sys, "argv", [MODULE_RUN, "coverage"])


@pytest.fixture()
def as_console(monkeypatch):
    """argv as the installed console script leaves it."""
    monkeypatch.setattr(sys, "argv", [CONSOLE_RUN, "coverage"])


# --- the helper itself -------------------------------------------------------

def test_the_console_script_names_itself(as_console):
    assert _self() == "crapkit"


def _named_file(spelled: str) -> str:
    """The path a spelled interpreter word names, its quotes read back."""
    if os.name == "nt":
        return spelled.replace('"', "")
    return shlex.split(spelled)[0]


def test_a_module_run_names_the_interpreter_that_is_running_it(as_module):
    """Not bare `python`: the reader may have no activated venv (the hook case),
    and the interpreter running this process is the one crapkit is installed in."""
    assert _self().endswith(" -m crapkit")
    assert os.path.samefile(_named_file(_self().removesuffix(" -m crapkit")), sys.executable)


@pytest.fixture()
def windows(monkeypatch):
    """invocation spelling paths for the Windows shells, on any OS."""
    monkeypatch.setattr(invocation, "os", SimpleNamespace(name="nt"))


@pytest.fixture()
def posix(monkeypatch):
    monkeypatch.setattr(invocation, "os", SimpleNamespace(name="posix"))


def test_a_windows_path_is_spelled_with_forward_slashes(windows):
    r"""Git Bash reads each bare backslash as an escape, so the next step
    `C:\wt\app\.venv\Scripts\python.exe -m crapkit coverage` ran as
    `C:wtapp.venvScriptspython.exe` and exited 127. cmd.exe, PowerShell and Git
    Bash all open a path spelled with forward slashes."""
    assert shell_path(r"C:\wt\app\.venv\Scripts\python.exe") == "C:/wt/app/.venv/Scripts/python.exe"


def test_a_windows_segment_holding_a_space_is_quoted_alone(windows):
    r"""A double quote at the start of a line is a string to PowerShell, and the
    `-m` after it a parse error, so `"C:\Program Files\...\python.exe" -m crapkit`
    never ran there. Quoting the segment keeps the line's first character bare,
    and cmd.exe, PowerShell and Git Bash each read the path back as one word."""
    spelled = shell_path(r"C:\Program Files (x86)\Python311-32\python.exe")

    assert spelled == 'C:/"Program Files (x86)"/Python311-32/python.exe'


def test_a_windows_segment_holding_a_shell_operator_is_quoted(windows):
    """cmd.exe ends a command word at `&`, `;`, `,` and `=`, and PowerShell at `;`
    and `(`: each segment holding one goes in double quotes."""
    assert shell_path(r"C:\a&b\c;d\e,f\g=h\py(3)\python.exe") == (
        'C:/"a&b"/"c;d"/"e,f"/"g=h"/"py(3)"/python.exe')


def test_a_windows_path_of_word_characters_is_left_bare(windows):
    assert shell_path(r"D:\tools\py-3.12_x64\~cache+\python.exe") == (
        "D:/tools/py-3.12_x64/~cache+/python.exe")


def test_a_posix_path_is_quoted_the_way_sh_reads_it(posix):
    """Double quotes let sh expand `$` and backticks inside them; single quotes
    hand every character on as it is."""
    assert shell_path("/home/a b/$HOME/bin/python") == "'/home/a b/$HOME/bin/python'"


def test_a_posix_path_without_anything_to_quote_is_left_bare(posix):
    assert shell_path("/usr/bin/python3") == "/usr/bin/python3"


def test_a_windows_module_run_names_the_interpreter_with_forward_slashes(windows, as_module,
                                                                         monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\wt\app\.venv\Scripts\python.exe")

    assert _self() == "C:/wt/app/.venv/Scripts/python.exe -m crapkit"


def test_a_posix_module_run_names_the_interpreter_bare(posix, as_module, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

    assert _self() == "/usr/bin/python3 -m crapkit"


# --- a spaced Windows interpreter: the same file, spelled without the space ---

windows_only = pytest.mark.skipif(os.name != "nt", reason="junctions and 8.3 names are Windows")


def _junction(link: Path, target: Path) -> Path:
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                   capture_output=True)
    return link


@windows_only
def test_an_interpreter_reached_through_a_spaced_link_is_named_by_its_target(tmp_path,
                                                                            monkeypatch):
    """No one line runs a venv's python.exe from a spaced path in both cmd.exe
    and PowerShell: the venv launcher ends its own name at the first space unless
    the line starts with a quote, and PowerShell reads a leading quote as a
    string. The directory the link points at spells the same interpreter with
    no space at all."""
    target = tmp_path / "real"
    (target / "Scripts").mkdir(parents=True)
    (target / "Scripts" / "python.exe").write_bytes(b"")
    link = _junction(tmp_path / "with space" / "venv", target)
    monkeypatch.setattr(sys, "argv", [MODULE_RUN])
    monkeypatch.setattr(sys, "executable", str(link / "Scripts" / "python.exe"))
    try:
        word = _self().removesuffix(" -m crapkit")
        named = _named_file(word)
    finally:
        os.rmdir(link)

    assert " " not in word
    assert Path(named) == (target / "Scripts" / "python.exe").resolve()


@windows_only
def test_a_spaced_interpreter_with_no_other_spelling_keeps_its_quoted_segments(tmp_path,
                                                                              monkeypatch):
    """A real directory holding a space: its 8.3 short name when the volume keeps
    one, else the path with that segment quoted."""
    python = tmp_path / "with space" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"")
    monkeypatch.setattr(sys, "argv", [MODULE_RUN])
    monkeypatch.setattr(sys, "executable", str(python))

    word = _self().removesuffix(" -m crapkit")

    assert " " not in word or word == shell_path(str(python))
    assert os.path.samefile(_named_file(word), python)


def test_an_empty_argv_falls_back_to_the_module_form(monkeypatch):
    """An embedded interpreter leaves argv empty. Nothing put a console script
    on PATH there either, so the module form is the honest answer."""
    monkeypatch.setattr(sys, "argv", [])

    assert _self().endswith(" -m crapkit")


# --- the messages ------------------------------------------------------------

def _init_next_step(_tmp):
    from crapkit.cli.admin import _next_step
    return _next_step({"src": ("python",)}, ())


def _queue_refusal(tmp):
    from crapkit.cli.queue import _scored_store
    with pytest.raises(CrapkitError) as raised:
        _scored_store(tmp)
    return str(raised.value)


def _verify_refusal(tmp):
    from crapkit.cli.verifying import _no_baseline
    return _no_baseline(tmp)


MESSAGES = pytest.mark.parametrize("message", [_init_next_step, _queue_refusal, _verify_refusal],
                                   ids=["init-next-step", "queue-refusal", "verify-refusal"])


@MESSAGES
def test_the_console_script_run_prescribes_the_console_script(message, tmp_path, as_console):
    assert "`crapkit coverage`" in message(tmp_path)


@MESSAGES
def test_the_module_run_prescribes_the_interpreter_that_is_running_it(message, tmp_path, as_module):
    """The red loop: with the venv's Scripts dir off PATH, every one of these
    lines named `crapkit coverage`, and the shell answered 127."""
    text = message(tmp_path)

    assert f"`{_self()} coverage`" in text
    assert "`crapkit coverage`" not in text
