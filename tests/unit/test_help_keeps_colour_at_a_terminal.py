"""A person at a terminal keeps 3.14's coloured help, and the switches that turn it off still work.

The fix for escape codes in pipes passes argparse color=False only when stdout
or stderr is not a terminal. At a terminal argparse keeps its own decision, so
NO_COLOR and TERM=dumb still turn the colour off there. argparse takes that
decision from stdout alone, while it prints usage errors to stderr: before the
fix, `crapkit coverage --bogus 2>log` typed at a terminal wrote escape codes
into the log. These run crapkit on a real pseudo-terminal, which Windows lacks.
"""
import os
import re
import sys

import pytest

from test_suite_pins_colour_and_width import run_in_a_terminal

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty, which Windows lacks"),
    pytest.mark.skipif(sys.version_info < (3, 14), reason="argparse colours help from Python 3.14 on"),
]
CRAPKIT = [sys.executable, "-m", "crapkit"]
_CSI = re.compile(r"\x1b\[[0-9;]*m")


def _terminal_env(**variables: str) -> dict:
    """A colour-capable terminal's environment plus `variables`."""
    return {**os.environ, "TERM": "xterm-256color", **variables}


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


def test_a_usage_error_sent_to_a_file_from_a_terminal_is_plain(tmp_path):
    log = tmp_path / "err.log"
    with log.open("wb") as stderr:
        code, shown = run_in_a_terminal([*CRAPKIT, "coverage", "--bogus"], 80, _terminal_env(),
                                        stderr=stderr)
    written = log.read_text(encoding="utf-8")

    assert code == 2, shown + written
    assert "usage: crapkit" in written, written
    assert "unrecognized arguments: --bogus" in written, written
    assert "\x1b" not in written, written
