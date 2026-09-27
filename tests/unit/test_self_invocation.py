"""The spelling every next-step and refusal uses for crapkit itself.

README tells a reader working from a source checkout to run `python -m crapkit`,
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
import sys
from pathlib import Path

import pytest

from uvx_process import CACHE_TAG, as_uvx as _as_uvx, cached_env, run_from

from crapkit import invocation
from crapkit.errors import CrapkitError
from crapkit.invocation import _self

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


def test_no_console_script_on_path_names_the_interpreter_that_is_running(off_path):
    """Not bare `python`: the reader may have no activated venv (the hook case),
    and the interpreter running this process is the one crapkit is installed in."""
    assert _self() == f"{invocation._quoted(invocation._forward(sys.executable))} -m crapkit"


def test_a_console_script_of_another_environment_is_not_named(tmp_path, monkeypatch):
    """A crapkit first on PATH that another interpreter installed runs another
    crapkit, maybe another version, so the message names this one's interpreter."""
    elsewhere = tmp_path / "other-env" / "bin"
    _launcher_in(elsewhere)
    monkeypatch.setattr(invocation, "_scripts_dirs", lambda: {(tmp_path / "env" / "bin").resolve()})
    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.chdir(tmp_path)

    assert _self().endswith(" -m crapkit")


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
    for byte, instead of linking it. A launcher holds the path of the
    interpreter it starts, so an equal copy starts this one. The red loop:
    `uv tool install crapkit` on Windows, then `crapkit init`, printed
    `next: run .../Scripts/python.exe -m crapkit coverage`."""
    _copied_launcher(tmp_path, monkeypatch, TOOL_LAUNCHER)

    assert _self() == "crapkit"


def test_a_copied_console_script_of_another_interpreter_is_not_named(tmp_path, monkeypatch):
    """A launcher that starts another interpreter holds another path, so its
    bytes differ from this install's launcher."""
    _copied_launcher(tmp_path, monkeypatch, TOOL_LAUNCHER.replace(b"tools", b"other"))

    assert _self().endswith(" -m crapkit")


def test_the_scripts_dirs_hold_this_interpreters_own_environment():
    import sysconfig

    own = Path(sysconfig.get_path("scripts")).resolve()

    assert own in invocation._scripts_dirs()


def test_a_windows_interpreter_is_spelled_with_forward_slashes():
    r"""Git Bash drops every backslash of `C:\venv\Scripts\python.exe`, and
    cmd.exe and PowerShell run the forward-slash form as well."""
    assert invocation._forward(r"C:\venv\Scripts\python.exe", "\\") == "C:/venv/Scripts/python.exe"
    assert invocation._forward("/usr/bin/python3", "/") == "/usr/bin/python3"


@pytest.mark.skipif(os.name != "nt", reason="sys.executable holds backslashes on Windows only")
def test_the_module_form_on_windows_holds_no_backslash(off_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\venv\Scripts\python.exe")

    assert _self() == "C:/venv/Scripts/python.exe -m crapkit"


def test_an_interpreter_path_holding_a_space_is_quoted(off_path, monkeypatch):
    """`C:/Program Files/Python311/python.exe` is an ordinary Windows install,
    and unquoted it reaches cmd.exe as `C:/Program` plus two arguments."""
    spaced = os.sep.join(["", "opt", "my python", "python.exe"])
    monkeypatch.setattr(sys, "executable", spaced)

    assert _self() == f'"{invocation._forward(spaced)}" -m crapkit'


def test_an_interpreter_path_without_a_space_is_left_bare(off_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

    assert _self() == "/usr/bin/python3 -m crapkit"


def test_a_uvx_run_names_uvx(as_uvx):
    """The red loop: `uvx crapkit init` printed `crapkit coverage`, and a shell
    with no crapkit on PATH answered 127. uvx is what the reader typed, and it
    finds the same cached environment again."""
    assert _self() == "uvx crapkit"


def test_a_cached_run_uv_did_not_start_names_its_interpreter(tmp_path, monkeypatch):
    """`pipx run crapkit` caches its environment the same way and sets no UV.
    Nothing names the runner, and the interpreter running this process resolves
    for as long as the cache keeps it."""
    env = cached_env(tmp_path / "pipx")
    run_from(env, monkeypatch)
    monkeypatch.setattr(sys, "executable", "/cache/pipx/0ef8/bin/python")

    assert _self() == "/cache/pipx/0ef8/bin/python -m crapkit"


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
