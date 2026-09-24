"""The help tests pass whatever colour and width the contributor's shell exports.

Several tests assert phrases of argparse's help and usage text. Python 3.14's
argparse colours that text when FORCE_COLOR or PYTHON_COLORS=1 is set, and every
version wraps it at COLUMNS, or at the terminal behind stdout when COLUMNS is
unset. At COLUMNS=40 "repo-relative" wraps as "repo-" / "relative", which no
whitespace fold undoes. So a shell that exported FORCE_COLOR, or `pytest -s` in a
narrow pane, turned these tests red, and the failure said nothing about crapkit.

tests/conftest.py now drops the colour variables and pins COLUMNS=80, the width
argparse uses in a pipe, before any test runs. A test that needs colour or a
width sets it itself. Each case below runs the help tests in a child pytest with
one variable exported, the way a contributor's shell hands it over.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hang_guard import HANG_SECONDS

ROOT = Path(__file__).resolve().parents[2]
HELP_TESTS = [
    "tests/unit/test_cli_argument_guards.py::test_help_prints_the_command_list",
    "tests/unit/test_cli_argument_guards.py::test_help_on_a_topic_prints_that_subcommands_help",
    "tests/unit/test_cli_argument_guards.py::test_help_names_the_topics_when_the_topic_is_not_one",
    "tests/unit/test_help_names_where_paths_are_read.py",
    "tests/unit/test_runner_refuses_negative_retention_flags.py",
    # These three read help text too, and passed under every row before the fixture.
    "tests/unit/test_cli_lazy_families.py::test_the_help_text_still_names_every_subcommand",
    "tests/unit/test_cli_docs_contract.py",
    "tests/unit/test_subcommand_rows_name_every_flag.py",
]
PYTEST = [sys.executable, "-m", "pytest", *HELP_TESTS, "-q", "--color=no", "-p", "no:randomly",
          "-p", "no:cacheprovider", "-o", "addopts="]
# The first three colour help on 3.14 and the next three wrap it narrow. The
# last two were green before the fixture, and stay green with it.
EXPORTED = {
    "FORCE_COLOR=1": {"FORCE_COLOR": "1"},
    "PYTHON_COLORS=1": {"PYTHON_COLORS": "1"},
    "TERM=dumb FORCE_COLOR=1": {"TERM": "dumb", "FORCE_COLOR": "1"},
    "COLUMNS=30": {"COLUMNS": "30"},
    "COLUMNS=40": {"COLUMNS": "40"},
    "COLUMNS=60": {"COLUMNS": "60"},
    "NO_COLOR=1 FORCE_COLOR=1": {"NO_COLOR": "1", "FORCE_COLOR": "1"},
    "COLUMNS=250": {"COLUMNS": "250"},
}


def _shell(exported: dict) -> dict:
    """This process's environment as a contributor's shell would pass it on:
    no width unless the row exports one, plus the row's variables."""
    env = {name: value for name, value in os.environ.items()
           if name not in ("COLUMNS", "LINES") and not name.startswith("PYTEST_")}
    return {**env, **exported}


@pytest.mark.parametrize("exported", sorted(EXPORTED))
def test_the_help_tests_pass_under_an_exported_colour_or_width(exported):
    done = subprocess.run(PYTEST, cwd=ROOT, env=_shell(EXPORTED[exported]), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=HANG_SECONDS)

    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]


def test_a_test_starts_with_no_colour_variable_and_eighty_columns():
    """What the conftest fixture promises every test, read from inside one."""
    names = ("FORCE_COLOR", "PY_COLORS", "PYTHON_COLORS", "NO_COLOR", "CLICOLOR_FORCE")

    assert [name for name in names if name in os.environ] == []
    assert os.environ["COLUMNS"] == "80"


# --- a narrow terminal, COLUMNS unset, `pytest -s` ---------------------------
#
# With `-s` pytest leaves stdout on the terminal, so argparse sizes help from
# the terminal itself. Default capture points fd 1 at a file, and help gets the
# 80-column fallback, which is why only `-s` went red.

def _read_all(master: int, deadline: float) -> str:
    """Everything the pty prints until the child closes it or the bound passes."""
    chunks = []
    while time.monotonic() < deadline:
        try:
            chunk = os.read(master, 65536)
        except OSError:  # Linux answers EIO once every writer has closed the pty
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", "replace")


def run_in_a_terminal(argv: list[str], columns: int, env: dict, stderr=None) -> tuple[int, str]:
    """`argv` with its stdin and stdout on a pty `columns` wide; stderr goes
    there too unless `stderr` names another file. Returns the exit code and
    what the terminal showed."""
    import fcntl
    import struct
    import termios

    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, columns, 0, 0))
    child = subprocess.Popen(argv, cwd=ROOT, env=env, stdin=slave, stdout=slave,
                             stderr=stderr or slave, start_new_session=True)
    os.close(slave)
    try:
        shown = _read_all(master, time.monotonic() + HANG_SECONDS)
        return child.wait(timeout=HANG_SECONDS), shown
    finally:
        os.close(master)
        child.kill()


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty, which Windows lacks")
@pytest.mark.parametrize("columns", [40, 60])
def test_the_help_tests_pass_with_capture_off_in_a_narrow_terminal(columns):
    code, shown = run_in_a_terminal([*PYTEST, "-s"], columns, _shell({}))

    assert code == 0, shown[-4000:]
