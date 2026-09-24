"""MCP tool text carries no escape codes, whatever colour the client's environment asks for.

A tool runs the CLI as a child with the server's environment, and a child that
prints nothing on stdout answers with its stderr: that stderr is the text the
model reads. The child's own interpreter colours what it writes there when the
environment says so. From Python 3.13 an uncaught traceback is coloured under
FORCE_COLOR or PYTHON_COLORS=1, and TERM=dumb does not stop FORCE_COLOR; from
3.14 argparse colours a usage error the same way. A `.crapkit/crap.sqlite`
that is not a database is one way to get that traceback today, and the model
got `File \\x1b[35m"<frozen runpy>"\\x1b[0m, line \\x1b[35m203\\x1b[0m`.

The relay reads the child's stderr through plaintext.strip_escapes. On 3.11
and 3.12 nothing is coloured, so the child rows pass there with or without it;
the relay rows at the top feed coloured text on every version.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit import mcp_server

# What a 3.14 child wrote to stderr under FORCE_COLOR=1, as captured.
COLOURED_USAGE = ("\x1b[1;34musage: \x1b[0m\x1b[1;35mcrapkit\x1b[0m [\x1b[32m-h\x1b[0m] "
                  "[\x1b[36m--version\x1b[0m]\n"
                  "\x1b[1;34mcrapkit: error:\x1b[0m \x1b[35munrecognized arguments: -x\x1b[0m\n")
COLOURED_TRACEBACK = ("Traceback (most recent call last):\n"
                      "  File \x1b[35m\"<frozen runpy>\"\x1b[0m, line \x1b[35m203\x1b[0m, "
                      "in \x1b[35m_run_module_as_main\x1b[0m\n"
                      "\x1b[1;35msqlite3.DatabaseError\x1b[0m: "
                      "\x1b[35mfile is not a database\x1b[0m\n")


def _cli_prints(monkeypatch, returncode: int, stdout: str, stderr: str) -> None:
    """The child the server spawns, answered without a process."""
    def _run(argv, **_kw):
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(mcp_server, "run_owned", _run)


TOML = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "pkg"\npaths = ["pkg"]\nlanguages = ["python"]\n'


def _measured(root: Path) -> Path:
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8")
    return root


@pytest.mark.parametrize("stderr, words", [
    (COLOURED_USAGE, ["usage: crapkit [-h] [--version]", "unrecognized arguments: -x"]),
    (COLOURED_TRACEBACK, ['File "<frozen runpy>", line 203, in _run_module_as_main',
                          "sqlite3.DatabaseError: file is not a database"]),
], ids=["argparse refusal", "traceback"])
def test_a_coloured_stderr_reaches_the_model_as_plain_text(stderr, words, monkeypatch, tmp_path):
    _cli_prints(monkeypatch, 2, "", stderr)

    result = mcp_server._call_tool(_measured(tmp_path), "list_runs", {})
    text = result["content"][0]["text"]

    assert result["isError"] is True
    assert "\x1b" not in text, text
    for line in words:
        assert line in text, text


def test_a_json_answer_on_stdout_is_relayed_as_printed(monkeypatch, tmp_path):
    """Only the stderr relay is filtered: the JSON a command prints on stdout
    already spells a control character as `\\u001b` and passes through as is."""
    printed = json.dumps({"runs": [], "note": "\x1b[31m", "schema": 1})
    _cli_prints(monkeypatch, 0, printed, "\x1b[31mwarning\x1b[0m")

    result = mcp_server._call_tool(_measured(tmp_path), "list_runs", {})

    assert result["content"][0]["text"] == printed
    assert result["structuredContent"]["note"] == "\x1b[31m"


# --- through a real child ------------------------------------------------------

COLOUR = {
    "FORCE_COLOR=1": {"FORCE_COLOR": "1"},
    "PYTHON_COLORS=1": {"PYTHON_COLORS": "1"},
    "TERM=dumb FORCE_COLOR=1": {"TERM": "dumb", "FORCE_COLOR": "1"},
    # controls: colour never turns on for these, fixed or not
    "no colour env": {},
    "NO_COLOR=1 FORCE_COLOR=1": {"NO_COLOR": "1", "FORCE_COLOR": "1"},
    "PYTHON_COLORS=0 FORCE_COLOR=1": {"PYTHON_COLORS": "0", "FORCE_COLOR": "1"},
}


@pytest.fixture(params=list(COLOUR))
def colour_env(request, monkeypatch):
    """The environment the server, and so its child, runs in."""
    for name in ("FORCE_COLOR", "NO_COLOR", "PYTHON_COLORS", "PY_COLORS", "TERM", "CLICOLOR_FORCE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in COLOUR[request.param].items():
        monkeypatch.setenv(name, value)


@pytest.fixture()
def corrupt_store(tmp_path) -> Path:
    """A measured repo whose snapshot store is not a SQLite database."""
    (tmp_path / ".crapkit").mkdir()
    (tmp_path / ".crapkit" / "crap.sqlite").write_bytes(b"not a sqlite database " * 8)
    return _measured(tmp_path)


def test_a_child_that_dies_uncaught_answers_without_escape_codes(corrupt_store, colour_env):
    """Python 3.13 and later colour this traceback; 3.11 and 3.12 do not."""
    result = mcp_server._call_tool(corrupt_store, "get_next_item", {})
    text = result["content"][0]["text"]

    assert result["isError"] is True
    assert text.strip(), "the child's stderr is the answer"
    assert "\x1b" not in text, text


def test_a_child_argparse_refusal_answers_without_escape_codes(tmp_path, colour_env):
    """No tool's argv reaches argparse as a refusal any more, so a tool entry
    with a flag the CLI lacks stands in for one. Python 3.14 colours it."""
    tool = {"name": "stand_in", "argv": ("worklist", "--no-such-flag"), "positional": (),
            "flags": {}, "json_flag": True}

    result = mcp_server._run_cli(tool, {}, str(_measured(tmp_path)))
    text = result["content"][0]["text"]

    assert result["isError"] is True
    assert "unrecognized arguments: --no-such-flag" in text, text
    assert "\x1b" not in text, text


@pytest.mark.skipif(sys.version_info < (3, 13), reason="tracebacks are coloured from 3.13")
def test_the_child_really_colours_its_traceback_here(corrupt_store, monkeypatch):
    """Proof the child rows above test something on this interpreter: the same
    child, run without the relay, writes escape codes under FORCE_COLOR."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PYTHON_COLORS", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    done = subprocess.run([sys.executable, "-m", "crapkit", "next-item", f"--repo={corrupt_store}"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300)

    assert "\x1b[" in done.stderr, done.stderr
