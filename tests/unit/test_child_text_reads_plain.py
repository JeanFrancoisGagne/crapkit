"""crapkit reads a lane child's text as plain text, whatever colour the child wrote.

A lane runs with crapkit's environment, so FORCE_COLOR or PY_COLORS make pytest
colour what it writes to the lane log and the junit report, and on Python 3.14
PYTHON_COLORS does the same to pytest's argparse usage error. crapkit reads that
text as data: `_DIAGNOSTIC` hoists the cause line with a match anchored at the
line start, `_missing_plugin_hint` looks for two substrings, and the refusal
quotes the tail to stderr, `--json` lane_failures and the pull-request comment.
A colour code in front of `E   ModuleNotFoundError` hid the cause, and the
quoted tail carried raw escape bytes to every reader.

The logs and the junit report below are the bytes pytest 9 writes under
FORCE_COLOR=1 (lane on 3.12) and PYTHON_COLORS=1 (lane on 3.14), escape for
escape; tests/e2e/test_lane_colour_reads_plain_e2e.py runs the real lanes.
"""
from pathlib import Path

import pytest

from crapkit.config import Lane
from crapkit.errors import ToolError

ESC = "\x1b"
RECORDED = Path(__file__).resolve().parent.parent / "fixtures" / "recorded"


# --- the stripper ------------------------------------------------------------

@pytest.mark.parametrize("coloured, plain", [
    ("\x1b[31mERROR\x1b[0m tests/test_a.py", "ERROR tests/test_a.py"),
    ("\x1b[1m\x1b[31mE   ModuleNotFoundError: No module named 'm'\x1b[0m\x1b[0m",
     "E   ModuleNotFoundError: No module named 'm'"),
    ("    \x1b[0m\x1b[94mimport\x1b[39;49;00m\x1b[90m \x1b[39;49;00m\x1b[04m\x1b[96mpkg\x1b[39;49;00m",
     "    import pkg"),
    ("\x1b[1;34musage: \x1b[0m\x1b[35m\x1b[1;35mpython -m pytest\x1b[0m", "usage: python -m pytest"),
    ("\x1b[?25lbusy\x1b[?25h", "busy"),
    ("\x1b[2K\rprogress 3/4", "\rprogress 3/4"),
    ("\x1b]8;;https://example.com/a\x07link\x1b]8;;\x07 after", "link after"),
    ("\x1b]0;window title\x1b\\text", "text"),
    ("\x1b]8;;cut off\nnext line", "\nnext line"),
    ("\x1bMup\x1b7\x1b8", "up"),
    ("\x1b(Bplain", "plain"),
    ("end\x1b", "end"),
    ("a\x1b\nb", "a\nb"),
], ids=["sgr", "bold-red-gutter", "pygments-tokens", "argparse-3.14", "private-params",
        "erase-line-keeps-cr", "osc8-bel", "osc-st", "osc-cut-at-line-end", "two-byte",
        "charset", "lone-at-end", "lone-before-newline"])
def test_strip_escapes_leaves_the_text_a_person_saw(coloured, plain):
    from crapkit.plaintext import strip_escapes

    assert strip_escapes(coloured) == plain


def test_text_without_an_escape_comes_back_unchanged():
    from crapkit.plaintext import strip_escapes

    text = "E   ImportError: café\tüber [31m #x1B[31m\r\nlast"

    assert strip_escapes(text) == text


@pytest.mark.parametrize("follower", [chr(code) for code in range(0x80)],
                         ids=[f"esc+{code:02x}" for code in range(0x80)])
def test_no_escape_byte_survives_whatever_follows_it(follower):
    """Every ESC starts a match, so none reaches a reader, whatever byte the
    child wrote after it."""
    from crapkit.plaintext import strip_escapes

    assert ESC not in strip_escapes(f"a\x1b{follower}b\x1b")


def test_the_junit_form_drops_the_escapes_pytest_spells_as_text():
    """XML 1.0 cannot hold ESC, so pytest's junitxml writes it as the six
    characters `#x1B`, and BEL as `#x07`."""
    from crapkit.plaintext import strip_junit_escapes

    text = ("#x1B[31mImportError while importing test module '/repo/tests/test_n3.py'.\n"
            "#x1B[1m#x1B[31mE   ModuleNotFoundError: No module named 'pkg.missing_mod'#x1B[0m#x1B[0m\n"
            "#x1B]8;;https://example.com#x07link#x1B]8;;#x07")

    assert strip_junit_escapes(text) == (
        "ImportError while importing test module '/repo/tests/test_n3.py'.\n"
        "E   ModuleNotFoundError: No module named 'pkg.missing_mod'\n"
        "link")


# --- the lane refusal --------------------------------------------------------

def _broken_log(modules: int = 15) -> list[str]:
    """What `pytest --cov` writes under FORCE_COLOR=1 when every test module
    fails at import: a traceback per module, then a summary block of ERROR
    lines long enough to push every `E   ` line out of a 500-character tail."""
    lines = ["$ python -m pytest tests -p no:cacheprovider --cov=pkg --cov-report=json:.crapkit/cov/py.json",
             "\x1b[1m============================= test session starts =============================\x1b[0m",
             f"collected 0 items / {modules} errors", "",
             "=================================== ERRORS ===================================="]
    for n in range(modules):
        lines += [f"\x1b[31m\x1b[1m__________________ ERROR collecting tests/test_broken_{n:02}.py ___________________\x1b[0m",
                  f"\x1b[31mImportError while importing test module '/repo/tests/test_broken_{n:02}.py'.",
                  "Hint: make sure your test modules/packages have valid Python names.",
                  "Traceback:",
                  f"\x1b[1m\x1b[31mtests/test_broken_{n:02}.py\x1b[0m:1: in <module>",
                  f"    \x1b[0m\x1b[94mimport\x1b[39;49;00m\x1b[90m \x1b[39;49;00m\x1b[04m\x1b[96mpkg.missing_{n:02}\x1b[39;49;00m",
                  f"\x1b[1m\x1b[31mE   ModuleNotFoundError: No module named 'pkg.missing_{n:02}'\x1b[0m\x1b[0m"]
    lines += ["\x1b[36m\x1b[1m=========================== short test summary info ===========================\x1b[0m"]
    lines += [f"\x1b[31mERROR\x1b[0m tests/test_broken_{n:02}.py" for n in range(modules)]
    lines += [f"!!!!!!!!!!!!!!!!!!! Interrupted: {modules} errors during collection !!!!!!!!!!!!!!!!!!!",
              f"\x1b[31m============================== \x1b[31m\x1b[1m{modules} errors\x1b[0m\x1b[31m in 0.62s\x1b[0m\x1b[31m ==============================\x1b[0m",
              "(exit 2)"]
    return lines


# pytest without pytest-cov rejects --cov. Lane on 3.12 under FORCE_COLOR=1:
# pytest paints the whole block red. Lane on 3.14 under PYTHON_COLORS=1 alone:
# argparse colours the usage line and pytest adds nothing.
_NO_COV = {
    "lane-3.12-FORCE_COLOR=1": [
        "$ python -m pytest --cov=pkg --cov-report=json:.crapkit/cov/py.json",
        "\x1b[31mERROR: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...]",
        "python -m pytest: error: unrecognized arguments: --cov=pkg --cov-report=json:.crapkit/cov/py.json",
        "  inifile: None", "  rootdir: /repo", "\x1b[0m", "(exit 4)"],
    "lane-3.14-PYTHON_COLORS=1": [
        "$ python -m pytest --cov=pkg --cov-report=json:.crapkit/cov/py.json",
        "ERROR: \x1b[1;34musage: \x1b[0m\x1b[35m\x1b[1;35mpython -m pytest\x1b[0m\x1b[35m [options] "
        "[file_or_dir] [file_or_dir] [...]\x1b[0m",
        "python -m pytest: error: unrecognized arguments: --cov=pkg --cov-report=json:.crapkit/cov/py.json",
        "  inifile: None", "  rootdir: /repo", "", "(exit 4)"],
}


def _py_lane() -> Lane:
    return Lane(name="py", command="python -m pytest --cov=pkg", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("pkg",))


def _refusal(tmp_path, lines: list[str], exit_code: int) -> tuple[str, Path]:
    from crapkit.lanes import _raise_no_artifact

    log = tmp_path / ".crapkit" / "lane-py.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    with pytest.raises(ToolError) as raised:
        _raise_no_artifact(tmp_path, _py_lane(), log, exit_code)
    return str(raised.value), log


def test_a_coloured_collection_failure_names_its_cause_first(tmp_path):
    message, _ = _refusal(tmp_path, _broken_log(), 2)

    assert "last output: E   ModuleNotFoundError: No module named 'pkg.missing_" in message, message


def test_a_coloured_lane_log_is_quoted_without_an_escape_byte(tmp_path):
    message, _ = _refusal(tmp_path, _broken_log(), 2)

    assert ESC not in message
    assert "ERROR tests/test_broken_14.py" in message, "the tail still quotes the summary block"


def test_the_lane_log_on_disk_keeps_its_colour(tmp_path):
    """Only the reader's copy is plain. A person tailing the log in a terminal
    or a CI viewer that renders colour still gets the child's own bytes."""
    lines = _broken_log()
    _, log = _refusal(tmp_path, lines, 2)

    assert log.read_text(encoding="utf-8") == "\n".join(lines)


@pytest.mark.parametrize("lines", list(_NO_COV.values()), ids=list(_NO_COV))
def test_the_pytest_cov_hint_fires_through_a_coloured_usage_error(tmp_path, lines):
    message, _ = _refusal(tmp_path, lines, 4)

    assert "the --cov flags come from the pytest-cov package" in message
    assert ESC not in message


def test_the_cause_hoisted_from_an_earlier_attempt_boundary_reads_plain(tmp_path):
    """A retried lane's banner is crapkit's own line and never coloured; the
    cause after it is the child's, and the scan reads it plain."""
    lines = ["$ python -m pytest --cov", "\x1b[1m\x1b[31mE   ImportError: first\x1b[0m", "(exit 2)", "",
             "--- attempt 2 ---", *_broken_log()[1:]]

    message, _ = _refusal(tmp_path, lines, 2)

    assert "E   ImportError: first" not in message, "attempt 1 was superseded"
    assert "last output: E   ModuleNotFoundError" in message


# --- the junit refusal -------------------------------------------------------

def _coloured_collection_junit() -> str:
    return (RECORDED / "junit_xdist_collection_error_force_color.xml").read_text(encoding="utf-8")


def test_the_recorded_report_carries_pytests_escaped_colour():
    """The fixture is what pytest-xdist wrote under FORCE_COLOR=1, so the
    refusal below reads the text a real report holds."""
    assert "#x1B[31mImportError while importing" in _coloured_collection_junit()


def test_a_coloured_junit_collection_error_is_quoted_as_plain_text():
    from crapkit.junitparse import suite_summary

    with pytest.raises(ToolError) as raised:
        suite_summary(_coloured_collection_junit())

    message = str(raised.value)
    assert "#x1B" not in message and ESC not in message, message
    assert "collection error: collection failure ImportError while importing test module" in message
    assert "E   ModuleNotFoundError: No module named 'pkg.missing_mod'" in message


def test_a_crashed_worker_is_still_named_when_its_text_is_coloured():
    from crapkit.junitparse import suite_summary

    report = ('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" errors="1" '
              'failures="0" skipped="0" tests="2"><testcase classname="tests.test_a" name="test_ok" time="0.1"/>'
              '<error message="#x1B[31mworker \'gw0\' crashed while running \'tests/test_b.py::test_x\'#x1B[0m"/>'
              '</testsuite></testsuites>')

    with pytest.raises(ToolError) as raised:
        suite_summary(report)

    assert "worker 'gw0' crashed while running 'tests/test_b.py::test_x'" in str(raised.value)
    assert "#x1B" not in str(raised.value)
