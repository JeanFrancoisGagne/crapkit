"""The spelling every next-step and refusal uses for crapkit itself.

README tells a reader working from a source checkout to run `python -P -m crapkit`,
and the pre-commit hook it documents spells the same form with an absolute
interpreter path because git runs hooks outside the activated venv. Neither of
those environments puts a `crapkit` on PATH. Both used to be answered with "run
`crapkit coverage`", so `init` finished by naming a command the shell it just
ran in exits 127 on.

So a message names `crapkit` only when PATH resolves it to a console script of
the interpreter running crapkit, and names that interpreter otherwise, spelled
with forward slashes so Git Bash runs it too
(test_agent_read_commands_run_in_git_bash.py runs it in every shell).

uvx starts the console script too, from an environment in uv's cache that only
the process uvx starts has on PATH. `uvx crapkit init` told its reader to run
`crapkit coverage`, and the shell answered 127.
"""
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from uvx_process import CACHE_TAG, as_uvx as _as_uvx, cached_env, run_from

from crapkit import invocation
from crapkit.errors import CrapkitError
from crapkit.invocation import _self, shell_arg, shell_path

LAUNCHER = "crapkit.exe" if os.name == "nt" else "crapkit"


def _launcher_in(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    launcher = directory / LAUNCHER
    launcher.write_bytes(b"#!/bin/sh\n")
    launcher.chmod(0o755)
    return launcher


@pytest.fixture(autouse=True)
def _outside_any_runner(tmp_path, monkeypatch):
    """An installed venv no runner started, whatever runs this suite: `uv run`
    leaves UV set, and a cached environment puts the prefix under a tag."""
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))


@pytest.fixture()
def on_path(tmp_path, monkeypatch):
    """PATH resolves `crapkit` to a console script this interpreter installed."""
    scripts = tmp_path / "env" / "Scripts"
    _launcher_in(scripts)
    monkeypatch.setattr(invocation, "_scripts_dirs", lambda: {scripts.resolve()})
    monkeypatch.setenv("PATH", str(scripts))
    monkeypatch.chdir(tmp_path)


@pytest.fixture()
def off_path(tmp_path, monkeypatch):
    """No `crapkit` on PATH: the source checkout and the git hook."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.chdir(tmp_path)


@pytest.fixture()
def as_uvx(tmp_path, monkeypatch):
    """argv, prefix and environment as `uvx crapkit` leaves them: the launcher in
    an environment under uv's cache, and uv's own path in UV."""
    _as_uvx(tmp_path, monkeypatch)


# --- the helper itself -------------------------------------------------------

def test_a_console_script_this_interpreter_installed_names_itself(on_path):
    assert _self() == "crapkit"


def _named_file(spelled: str) -> str:
    """The path a spelled interpreter word names, its quotes read back."""
    if os.name == "nt":
        return spelled.replace('"', "")
    return shlex.split(spelled)[0]


def test_no_console_script_on_path_names_the_interpreter_that_is_running(off_path):
    """Not bare `python`: the reader may have no activated venv (the hook case),
    and the interpreter running this process is the one crapkit is installed in."""
    assert _self().endswith(" -P -m crapkit")
    assert os.path.samefile(_named_file(_self().removesuffix(" -P -m crapkit")), sys.executable)


def test_a_console_script_of_another_environment_is_not_named(tmp_path, monkeypatch):
    """A crapkit first on PATH that another interpreter installed runs another
    crapkit, maybe another version, so the message names this one's interpreter."""
    elsewhere = tmp_path / "other-env" / "bin"
    _launcher_in(elsewhere)
    monkeypatch.setattr(invocation, "_scripts_dirs", lambda: {(tmp_path / "env" / "bin").resolve()})
    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.chdir(tmp_path)

    assert _self().endswith(" -P -m crapkit")


@pytest.mark.skipif(os.name == "nt", reason="pipx and uv tool link console scripts on POSIX only")
def test_a_linked_console_script_counts_where_it_points(tmp_path, monkeypatch):
    """pipx and uv tool put a symlink on PATH that points into the tool's venv."""
    target = _launcher_in(tmp_path / "tool-venv" / "bin")
    shelf = tmp_path / "local-bin"
    shelf.mkdir()
    (shelf / LAUNCHER).symlink_to(target)
    monkeypatch.setattr(invocation, "_scripts_dirs", lambda: {target.parent.resolve()})
    monkeypatch.setenv("PATH", str(shelf))

    assert _self() == "crapkit"


TOOL_LAUNCHER = b"MZ launcher #!C:\\tools\\crapkit\\Scripts\\python.exe\n"


def _copied_launcher(tmp_path, monkeypatch, shelf_bytes: bytes) -> None:
    """A tool env's launcher, and a file of `shelf_bytes` in a bin dir first on PATH."""
    target = _launcher_in(tmp_path / "tools" / "crapkit" / "Scripts")
    target.write_bytes(TOOL_LAUNCHER)
    shelf = _launcher_in(tmp_path / "bin")
    shelf.write_bytes(shelf_bytes)
    monkeypatch.setattr(invocation, "_scripts_dirs", lambda: {target.parent.resolve()})
    monkeypatch.setenv("PATH", str(shelf.parent))
    monkeypatch.chdir(tmp_path)


def test_a_copied_console_script_counts_when_its_bytes_are_this_installs(tmp_path, monkeypatch):
    """uv tool and pipx on Windows copy the launcher into their bin dir, byte
    for byte, since a symlink there needs Developer Mode. A launcher holds the
    path of the interpreter it starts, so an equal copy starts this one. The
    red loop: `uv tool install crapkit` on Windows, then `crapkit init`,
    printed `next: run .../Scripts/python.exe -m crapkit coverage` where
    README prints `crapkit coverage`."""
    _copied_launcher(tmp_path, monkeypatch, TOOL_LAUNCHER)

    assert _self() == "crapkit"


def test_a_copied_console_script_of_another_interpreter_is_not_named(tmp_path, monkeypatch):
    """A launcher that starts another interpreter holds another path, so its
    bytes differ from this install's launcher."""
    _copied_launcher(tmp_path, monkeypatch, TOOL_LAUNCHER.replace(b"tools", b"other"))

    assert _self().endswith(" -P -m crapkit")


def test_the_scripts_dirs_hold_this_interpreters_own_environment():
    import sysconfig

    own = Path(sysconfig.get_path("scripts")).resolve()

    assert own in invocation._scripts_dirs()


@pytest.mark.skipif(os.name != "nt", reason="sys.executable holds backslashes on Windows only")
def test_the_module_form_on_windows_holds_no_backslash(off_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\venv\Scripts\python.exe")

    assert _self() == "C:/venv/Scripts/python.exe -P -m crapkit"


def test_an_interpreter_path_without_a_space_is_left_bare(off_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

    assert _self() == "/usr/bin/python3 -P -m crapkit"


def test_a_uvx_run_names_uvx(as_uvx):
    """The red loop: `uvx crapkit init` printed `crapkit coverage`, and a shell
    with no crapkit on PATH answered 127. uvx is what the reader typed, and it
    finds the same cached environment again."""
    assert _self() == "uvx crapkit"


def test_a_uvx_run_looks_a_tool_up_on_the_path_its_reader_has(tmp_path, monkeypatch):
    """uvx puts its cached bin first on this process's PATH alone, so a lookup
    reads PATH without it: the reader's shell and the plugin's hooks never see
    that launcher."""
    launcher_bin = _as_uvx(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", os.pathsep.join([str(launcher_bin), "/usr/local/bin", "/usr/bin"]))

    assert invocation.path_without_own_cache() == os.pathsep.join(["/usr/local/bin", "/usr/bin"])


def test_a_run_from_no_cache_looks_a_tool_up_on_its_own_path():
    assert invocation.path_without_own_cache() is None


def test_a_cached_run_uv_did_not_start_names_its_interpreter(tmp_path, monkeypatch):
    """`pipx run crapkit` caches its environment the same way and sets no UV.
    Nothing names the runner, and the interpreter running this process resolves
    for as long as the cache keeps it."""
    env = cached_env(tmp_path / "pipx")
    run_from(env, monkeypatch)
    monkeypatch.setattr(sys, "executable", "/cache/pipx/0ef8/bin/python")

    assert _self() == "/cache/pipx/0ef8/bin/python -P -m crapkit"


def test_an_installed_tool_under_uv_names_the_console_script(tmp_path, monkeypatch):
    """`uv tool install crapkit` tags the tool's own environment, as uv tags
    every environment it creates, and puts the console script on PATH. A shell
    `uv run` started still carries UV. Only a tag above the environment makes
    it a cache."""
    env = tmp_path / "share" / "uv" / "tools" / "crapkit"
    scripts = _launcher_in(env / "bin").parent
    (env / "CACHEDIR.TAG").write_text(CACHE_TAG, encoding="utf-8")
    run_from(env, monkeypatch)
    monkeypatch.setenv("UV", "/usr/local/bin/uv")
    monkeypatch.setattr(invocation, "_scripts_dirs", lambda: {scripts.resolve()})
    monkeypatch.setenv("PATH", str(scripts))

    assert _self() == "crapkit"


@pytest.fixture()
def windows(monkeypatch):
    """invocation spelling paths for the Windows shells, on any OS."""
    monkeypatch.setattr(invocation, "os", SimpleNamespace(name="nt"))


@pytest.fixture()
def posix(monkeypatch):
    monkeypatch.setattr(invocation, "os", SimpleNamespace(name="posix"))


def test_a_windows_path_is_spelled_with_forward_slashes(windows):
    r"""Git Bash reads each bare backslash as an escape, so the next step
    `C:\proj\app\.venv\Scripts\python.exe -m crapkit coverage` ran as
    `C:projapp.venvScriptspython.exe` and exited 127. cmd.exe, PowerShell and Git
    Bash all open a path spelled with forward slashes."""
    assert shell_path(r"C:\proj\app\.venv\Scripts\python.exe") == "C:/proj/app/.venv/Scripts/python.exe"


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


def test_a_windows_argument_that_needs_quotes_goes_in_one_pair(windows):
    """After the command word, PowerShell ends a word that opens with a quote at
    the closing quote: `"my repos"/app` is two arguments there. A whole quoted
    word is one argument in cmd.exe, PowerShell and Git Bash."""
    assert shell_arg(r"my repos\app") == '"my repos/app"'
    assert shell_arg(r"C:\a&b\x") == '"C:/a&b/x"'


def test_a_windows_argument_of_word_characters_is_left_bare(windows):
    assert shell_arg(r"C:\work\app") == "C:/work/app"


def test_a_posix_argument_is_quoted_the_way_sh_reads_it(posix):
    assert shell_arg("my repos/$app") == "'my repos/$app'"


def test_a_windows_module_run_names_the_interpreter_with_forward_slashes(windows, monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\proj\app\.venv\Scripts\python.exe")

    assert invocation._module_form() == "C:/proj/app/.venv/Scripts/python.exe -P -m crapkit"


def test_a_posix_module_run_names_the_interpreter_bare(posix, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

    assert invocation._module_form() == "/usr/bin/python3 -P -m crapkit"


# --- a spaced Windows interpreter: the same file, spelled without the space ---

windows_only = pytest.mark.skipif(os.name != "nt", reason="junctions and 8.3 names are Windows")


def _junction(link: Path, target: Path) -> Path:
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                   capture_output=True)
    return link


@windows_only
def test_an_interpreter_reached_through_a_spaced_link_is_named_by_its_target(tmp_path, off_path,
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
    monkeypatch.setattr(sys, "executable", str(link / "Scripts" / "python.exe"))
    try:
        word = _self().removesuffix(" -P -m crapkit")
        named = _named_file(word)
    finally:
        os.rmdir(link)

    assert " " not in word
    assert Path(named) == (target / "Scripts" / "python.exe").resolve()


@windows_only
def test_a_spaced_interpreter_with_no_other_spelling_keeps_its_quoted_segments(tmp_path, off_path,
                                                                              monkeypatch):
    """A real directory holding a space: its 8.3 short name when the volume keeps
    one, else the path with that segment quoted and the line's first character
    bare. That line runs in PowerShell, pwsh and Git Bash; for a venv's launcher
    cmd.exe loses it, and docs/adr/0003 says why that trade was taken."""
    python = tmp_path / "with space" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(python))

    word = _self().removesuffix(" -P -m crapkit")

    quoted_alone = word.endswith('/"with space"/python.exe') and not word.startswith('"')
    assert " " not in word or quoted_alone, word
    assert os.path.samefile(_named_file(word), python)


SPACED = r"C:\with space\venv\Scripts\python.exe"
SHORT = r"C:\WITHSP~1\venv\Scripts\python.exe"


@pytest.mark.parametrize(("linked", "short", "spelled"), [
    (r"C:\real\venv\Scripts\python.exe", SHORT, "C:/real/venv/Scripts/python.exe"),
    (SPACED, SHORT, "C:/WITHSP~1/venv/Scripts/python.exe"),
    (SPACED, SPACED, 'C:/"with space"/venv/Scripts/python.exe')])
def test_a_spaced_interpreter_takes_the_first_spelling_without_a_space(windows, monkeypatch,
                                                                       linked, short, spelled):
    """The link's target first, then the 8.3 short name, then the path with its
    spaced segment quoted. The two lookups are faked so every OS runs the
    choice; the Windows-only tests above run the real ones."""
    monkeypatch.setattr(invocation, "_unlinked", lambda path: linked)
    monkeypatch.setattr(invocation, "_short_name", lambda path: short)

    assert invocation.interpreter_word(SPACED) == spelled


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
def test_a_console_script_on_path_prescribes_the_console_script(message, tmp_path, on_path):
    assert "`crapkit coverage`" in message(tmp_path)


@MESSAGES
def test_no_console_script_on_path_prescribes_the_interpreter_that_is_running(message, tmp_path,
                                                                             off_path):
    """The red loop: with the venv's Scripts dir off PATH, every one of these
    lines named `crapkit coverage`, and the shell answered 127."""
    text = message(tmp_path)

    assert f"`{_self()} coverage`" in text
    assert "`crapkit coverage`" not in text


@MESSAGES
def test_a_uvx_run_prescribes_uvx(message, tmp_path, as_uvx):
    assert "`uvx crapkit coverage`" in message(tmp_path)


def test_the_readme_uvx_transcript_quotes_the_next_step_uvx_prints(as_uvx):
    """README's `uvx crapkit init` block showed `next: run \\`crapkit coverage\\``,
    the line a reader then pasted into a shell with no crapkit on PATH."""
    from types import SimpleNamespace

    from crapkit.cli.admin import _next_step

    line = _next_step({"src": ("typescript",)}, (SimpleNamespace(name="js"),))
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    block = readme.split("$ uvx crapkit init\n", 1)[1].split("\n\n", 1)[0]

    assert line in block.splitlines(), block
