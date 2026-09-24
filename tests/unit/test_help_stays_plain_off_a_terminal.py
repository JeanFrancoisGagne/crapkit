"""Help and usage text carry no escape codes unless they are going to a terminal.

Python 3.14's argparse colours help and usage. It asks PYTHON_COLORS, then
NO_COLOR, then FORCE_COLOR, and only then whether stdout is a terminal, so a
shell that exports FORCE_COLOR made `crapkit help` print escape codes into a
pipe, and every capture that read the text as data (an agent, this suite)
matched against `\\x1b[1;34musage: \\x1b[0m` instead of `usage:`. argparse also
takes that one decision from stdout while it prints usage errors to stderr, so a
terminal user who sent stderr to a file got the codes there. On 3.11 to 3.13 the
end-to-end tests below pass either way: there is no colour to strip.
"""
import io
import sys

import pytest

from crapkit.cli import main
from crapkit.cli import parser


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture()
def forced_color(monkeypatch):
    """The environment that turned the colour on: FORCE_COLOR set, and nothing
    that argparse asks before it."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PYTHON_COLORS", raising=False)


def test_help_in_a_pipe_is_plain_when_force_color_is_set(forced_color, capsys):
    code = main(["help", "coverage"])
    out = capsys.readouterr().out

    assert code == 0
    assert "usage: crapkit coverage" in out, out
    assert "\x1b" not in out, out


def test_subcommand_help_in_a_pipe_is_plain_when_force_color_is_set(forced_color, capsys):
    with pytest.raises(SystemExit) as stop:
        main(["ratchet", "seed", "--help"])
    out = capsys.readouterr().out

    assert stop.value.code == 0
    assert "\x1b" not in out, out


def test_a_usage_error_in_a_pipe_is_plain_when_force_color_is_set(forced_color, capsys):
    with pytest.raises(SystemExit) as stop:
        main(["coverage", "--no-such-flag"])
    err = capsys.readouterr().err

    assert stop.value.code == 2
    assert "error: unrecognized arguments: --no-such-flag" in err, err
    assert "\x1b" not in err, err


def test_a_usage_error_sent_to_a_file_is_plain_when_stdout_is_a_terminal(
        forced_color, capsys, monkeypatch):
    """`crapkit coverage --bogus 2>log` from a terminal: argparse asks stdout,
    stdout is a terminal, and the log file got the escape codes."""
    monkeypatch.setattr(sys, "stdout", _Terminal())
    with pytest.raises(SystemExit):
        main(["coverage", "--no-such-flag"])
    err = capsys.readouterr().err

    assert "error: unrecognized arguments: --no-such-flag" in err, err
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
