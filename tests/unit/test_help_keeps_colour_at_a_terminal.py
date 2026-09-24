"""A person at a terminal keeps 3.14's coloured help, and the switches that turn it off still work.

The fix for escape codes in pipes passes argparse color=False only when stdout
or stderr is not a terminal. At a terminal argparse keeps its own decision, so
NO_COLOR and TERM=dumb still turn the colour off there. argparse takes that
decision from stdout alone, while it prints usage errors to stderr: before the
fix, `crapkit coverage --bogus 2>log` typed at a terminal wrote escape codes
into the log. These run crapkit on a real pseudo-terminal, which Windows lacks.
Only the first test needs 3.14; the rest pin plain text on every version.
"""
import os
import re
import sys

import pytest

from test_suite_pins_colour_and_width import run_in_a_terminal

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="needs a POSIX pty, which Windows lacks")
CRAPKIT = [sys.executable, "-m", "crapkit"]
_CSI = re.compile(r"\x1b\[[0-9;]*m")


def _terminal_env(**variables: str) -> dict:
    """A colour-capable terminal's environment plus `variables`."""
    return {**os.environ, "TERM": "xterm-256color", **variables}


@pytest.mark.skipif(sys.version_info < (3, 14), reason="argparse colours help from Python 3.14 on")
def test_help_at_a_terminal_keeps_its_colour():
    code, shown = run_in_a_terminal([*CRAPKIT, "--help"], 80, _terminal_env())

    assert code == 0, shown
    assert "\x1b[" in shown, shown
    assert "usage: crapkit" in _CSI.sub("", shown), shown


@pytest.mark.parametrize("variables", [{"NO_COLOR": "1"}, {"TERM": "dumb"}],
                         ids=["NO_COLOR=1", "TERM=dumb"])
def test_the_usual_switches_still_turn_colour_off_at_a_terminal(variables):
    code, shown = run_in_a_terminal([*CRAPKIT, "--help"], 80, _terminal_env(**variables))

    assert code == 0, shown
    assert "usage: crapkit" in shown, shown
    assert "\x1b" not in shown, shown


# The first row is the one that wrote escape codes before the fix; the other
# three were plain already and must stay so.
_TERMINAL_EXPORTS = {
    "no colour variable": {},
    "NO_COLOR=1": {"NO_COLOR": "1"},
    "TERM=dumb": {"TERM": "dumb"},
    "PYTHON_COLORS=0": {"PYTHON_COLORS": "0"},
}


@pytest.mark.parametrize("exported", sorted(_TERMINAL_EXPORTS))
@pytest.mark.parametrize("argv, code, error", [
    (["coverage", "--bogus"], 2, "unrecognized arguments: --bogus"),
    (["inventry"], 2, "invalid choice: 'inventry'"),
    (["help", "nosuch"], 3, "no subcommand 'nosuch'"),
], ids=["unknown-flag", "misspelled-subcommand", "unknown-help-topic"])
def test_an_error_sent_to_a_file_from_a_terminal_is_plain(argv, code, error, exported, tmp_path):
    """`crapkit ... 2>log` typed at a terminal. The unknown help topic is
    crapkit's own message, not argparse's, and carries no usage block."""
    log = tmp_path / "err.log"
    with log.open("wb") as stderr:
        exit_code, shown = run_in_a_terminal([*CRAPKIT, *argv], 80,
                                             _terminal_env(**_TERMINAL_EXPORTS[exported]),
                                             stderr=stderr)
    written = log.read_text(encoding="utf-8")

    assert exit_code == code, shown + written
    assert error in written, written
    assert "\x1b" not in written, written
