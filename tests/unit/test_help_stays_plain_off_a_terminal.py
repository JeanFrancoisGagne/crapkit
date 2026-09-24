"""Help and usage text carry no escape codes unless they are going to a terminal.

Python 3.14's argparse colours help and usage. It asks PYTHON_COLORS, then
NO_COLOR, then FORCE_COLOR, and only then whether stdout is a terminal, so a
shell that exports FORCE_COLOR or PYTHON_COLORS=1 made `crapkit help` print
escape codes into a pipe, and every capture that read the text as data (an
agent, this suite) matched against `\\x1b[1;34musage: \\x1b[0m` instead of
`usage:`. TERM=dumb did not stop it, because FORCE_COLOR is asked first.
argparse also takes that one decision from stdout while it prints usage errors
to stderr, so a terminal user who sent stderr to a file got the codes there.
On 3.11 to 3.13 the end-to-end tests below pass either way: there is no colour
to strip.
"""
import io
import os
import subprocess
import sys

import pytest

from crapkit.cli import main
from crapkit.cli import parser
from hang_guard import HANG_SECONDS


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


# Each export a shell uses to ask for colour. 3.14's argparse obeys all of them
# but CLICOLOR_FORCE, which other tools read. FORCE_COLOR counts when it is set
# to anything, "0" included.
_COLOUR_ON = {
    "FORCE_COLOR": {"FORCE_COLOR": "1"},
    "FORCE_COLOR=0": {"FORCE_COLOR": "0"},
    "PYTHON_COLORS": {"PYTHON_COLORS": "1"},
    "TERM-dumb-FORCE_COLOR": {"TERM": "dumb", "FORCE_COLOR": "1"},
    "CLICOLOR_FORCE": {"CLICOLOR_FORCE": "1"},
}
# Exports under which argparse itself stays plain: NO_COLOR and PYTHON_COLORS=0
# outrank FORCE_COLOR. The fix must leave these plain too.
_COLOUR_OFF = {
    "NO_COLOR-FORCE_COLOR": {"NO_COLOR": "1", "FORCE_COLOR": "1"},
    "PYTHON_COLORS=0-FORCE_COLOR": {"PYTHON_COLORS": "0", "FORCE_COLOR": "1"},
}
_ASKED = ("FORCE_COLOR", "NO_COLOR", "PYTHON_COLORS", "TERM", "CLICOLOR_FORCE")


def _export(monkeypatch, variables: dict) -> None:
    """`variables`, and no other variable a colour decision reads."""
    for name in _ASKED:
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


@pytest.fixture(params=sorted(_COLOUR_ON))
def forced_color(request, monkeypatch):
    """An environment that asked for colour, and nothing argparse asks before
    it that would turn it back off."""
    _export(monkeypatch, _COLOUR_ON[request.param])


@pytest.fixture(params=sorted(_COLOUR_ON) + sorted(_COLOUR_OFF))
def colour_env(request, monkeypatch):
    """Every row above: the exports that ask for colour, then the controls."""
    _export(monkeypatch, {**_COLOUR_ON, **_COLOUR_OFF}[request.param])


def _exit_code(argv: list[str]) -> int:
    """main()'s answer, whether it returns it or argparse exits with it."""
    try:
        return main(argv)
    except SystemExit as stop:
        return stop.code


@pytest.mark.parametrize("argv", [
    ["--help"],
    ["help"],
    ["help", "coverage"],
    ["coverage", "--help"],
    ["ratchet", "seed", "--help"],
], ids=" ".join)
def test_help_in_a_pipe_is_plain_whatever_the_colour_variables_say(argv, colour_env, capsys):
    code = _exit_code(argv)
    out = capsys.readouterr().out

    assert code == 0
    assert "usage: crapkit" in out, out
    assert "\x1b" not in out, out


@pytest.mark.parametrize("name", sorted(parser._help_topics(parser.build_parser())))
def test_every_subcommands_help_in_a_pipe_is_plain_when_colour_is_forced(
        name, forced_color, capsys):
    """No subcommand parser decides colour for itself: each one inherits the
    root's keyword."""
    code = _exit_code([name, "--help"])
    out = capsys.readouterr().out

    assert code == 0
    assert f"usage: crapkit {name}" in out, out
    assert "\x1b" not in out, out


@pytest.mark.parametrize("argv, error", [
    (["coverage", "--no-such-flag"], "unrecognized arguments: --no-such-flag"),
    (["nonesuch"], "invalid choice: 'nonesuch'"),
    (["coverage", "--repo"], "expected one argument"),
    (["next-item", "--top", "five"], "invalid int value: 'five'"),
], ids=["unknown-flag", "unknown-subcommand", "missing-value", "wrong-type"])
def test_a_usage_error_in_a_pipe_is_plain_whatever_the_colour_variables_say(
        argv, error, colour_env, capsys):
    code = _exit_code(argv)
    err = capsys.readouterr().err

    assert code == 2
    assert error in err, err
    assert "\x1b" not in err, err


def test_a_usage_error_sent_to_a_file_is_plain_when_stdout_is_a_terminal(
        forced_color, capsys, monkeypatch):
    """`crapkit coverage --bogus 2>log` from a terminal: argparse asks stdout,
    stdout is a terminal, and the log file got the escape codes."""
    monkeypatch.setattr(sys, "stdout", _Terminal())
    code = _exit_code(["coverage", "--no-such-flag"])
    err = capsys.readouterr().err

    assert code == 2
    assert "error: unrecognized arguments: --no-such-flag" in err, err
    assert "\x1b" not in err, err


def test_a_claude_hook_flag_this_build_lacks_reaches_the_model_without_escape_codes(
        forced_color, capsys, monkeypatch):
    """A plugin newer than the installed CLI can pass the hook a flag this build
    does not define, and Claude Code hands whatever the hook prints on stderr to
    the model. This pins only that the text is plain, not what the hook says."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    _exit_code(["claude-hook", "--protocol", "1", "--budget", "5"])
    err = capsys.readouterr().err

    assert "\x1b" not in err, err


@pytest.mark.parametrize("columns", ["40", "250"])
@pytest.mark.parametrize("argv", [["--help"], ["coverage", "--help"]], ids=" ".join)
def test_help_in_a_pipe_is_plain_at_any_width(argv, columns, forced_color, capsys, monkeypatch):
    """COLUMNS moves where help wraps, never whether it is coloured."""
    monkeypatch.setenv("COLUMNS", columns)
    code = _exit_code(argv)
    out = capsys.readouterr().out

    assert code == 0
    assert "usage: crapkit" in out, out
    assert "\x1b" not in out, out


@pytest.mark.parametrize("argv, stream", [
    (["--help"], "stdout"),
    (["coverage", "--no-such-flag"], "stderr"),
], ids=["help", "usage-error"])
def test_a_crapkit_process_writes_plain_text_into_a_pipe_when_colour_is_forced(
        argv, stream, forced_color):
    """The same through a real process, whose stdout and stderr are OS pipes."""
    done = subprocess.run([sys.executable, "-m", "crapkit", *argv], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=HANG_SECONDS)

    assert "usage: crapkit" in getattr(done, stream), done.stdout + done.stderr
    assert "\x1b" not in done.stdout + done.stderr, done.stdout + done.stderr


def _launcher(directory) -> str:
    """A `crapkit` that runs this checkout's CLI with this interpreter, spelled
    the way this platform's shell starts one."""
    if os.name == "nt":
        launcher = directory / "crapkit.bat"
        launcher.write_text(f'@"{sys.executable}" -m crapkit %*\n', encoding="utf-8")
        return str(launcher)
    launcher = directory / "crapkit"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m crapkit "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    return str(launcher)


def test_doctors_version_probe_reads_the_version_when_colour_is_forced(forced_color, tmp_path):
    """`doctor --plugin-root` runs `crapkit --version` and reads "crapkit X" from
    it. argparse never colours that line, and this pins it."""
    from crapkit.cli import admin

    version = parser._version_line().split()[1]

    assert admin._probed_cli_version(_launcher(tmp_path)) == version


# --- the keyword the root parser is built with ---------------------------------

def test_before_3_14_the_parser_gets_no_color_keyword():
    """argparse before 3.14 has no `color` parameter and raises TypeError on one."""
    assert parser._color_kwargs((3, 13), (io.StringIO(), io.StringIO())) == {}


def test_on_a_terminal_argparse_keeps_its_own_decision():
    """NO_COLOR, PYTHON_COLORS and a console without escape support still turn
    colour off there; crapkit does not overrule them."""
    assert parser._color_kwargs((3, 14), (_Terminal(), _Terminal())) == {}


@pytest.mark.parametrize("stdout, stderr", [
    (io.StringIO(), _Terminal()),
    (_Terminal(), io.StringIO()),
    (None, None),
], ids=["stdout-piped", "stderr-piped", "no-streams"])
def test_off_a_terminal_colour_is_off(stdout, stderr):
    """`None` is what pythonw hands a process for both streams."""
    assert parser._color_kwargs((3, 14), (stdout, stderr)) == {"color": False}
