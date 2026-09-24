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
import sys

import pytest

from crapkit.cli import main
from crapkit.cli import parser


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


# Each environment that turns 3.14's colour on in a pipe.
_COLOUR_ON = {
    "FORCE_COLOR": {"FORCE_COLOR": "1"},
    "PYTHON_COLORS": {"PYTHON_COLORS": "1"},
    "TERM-dumb-FORCE_COLOR": {"TERM": "dumb", "FORCE_COLOR": "1"},
}


@pytest.fixture(params=sorted(_COLOUR_ON))
def forced_color(request, monkeypatch):
    """An environment that turned the colour on, and nothing argparse asks
    before it that would turn it back off."""
    for name in ("FORCE_COLOR", "NO_COLOR", "PYTHON_COLORS", "TERM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in _COLOUR_ON[request.param].items():
        monkeypatch.setenv(name, value)


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
def test_help_in_a_pipe_is_plain_when_colour_is_forced(argv, forced_color, capsys):
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
def test_a_usage_error_in_a_pipe_is_plain_when_colour_is_forced(
        argv, error, forced_color, capsys):
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
