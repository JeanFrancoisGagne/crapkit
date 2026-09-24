"""When the console entry starts the command again in UTF-8 mode, and with
which command line (crapkit.cli._restart_in_utf8_mode).

Under a POSIX locale whose filesystem encoding is not UTF-8, Python spelled
`pkg/café.py` back to the OS as b"pkg/caf\\xe9.py", a file that does not
exist. tests/e2e/test_latin1_locale_paths_e2e.py runs the real restart under a
Latin-1 locale on Linux; this file pins the decision and the exec on every OS,
through a stand-in `os` and `sys`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from crapkit import cli

PYTHON = "/usr/bin/python3"
# The bytes the shell passed, `pkg/café.py` among them.
ARGV = (PYTHON.encode(), b"-m", b"crapkit", b"rescore", b"pkg/caf\xc3\xa9.py")


def _stand_ins(monkeypatch, *, name="posix", utf8_mode=0, xoptions=None, encoding="iso8859-1",
               executable=PYTHON, argv=ARGV):
    """`os` and `sys` as a process under `encoding` sees them: it holds each
    argument decoded with that codec (mbcs is Windows' own, held here as UTF-8)."""
    held = "utf-8" if encoding == "mbcs" else encoding
    execs = []
    fake_os = SimpleNamespace(name=name, execv=lambda path, args: execs.append((path, args)),
                              fsencode=lambda value: value.encode(held, "surrogateescape"))
    fake_sys = SimpleNamespace(flags=SimpleNamespace(utf8_mode=utf8_mode), _xoptions=xoptions or {},
                               getfilesystemencoding=lambda: encoding, executable=executable,
                               orig_argv=[arg.decode(held, "surrogateescape") for arg in argv])
    monkeypatch.setattr(cli, "os", fake_os)
    monkeypatch.setattr(cli, "sys", fake_sys)
    return execs


@pytest.mark.parametrize("state, restarts", [
    ({}, True),
    ({"encoding": "ascii"}, True),
    ({"encoding": "utf-8"}, False),
    ({"encoding": "UTF8"}, False),
    ({"utf8_mode": 1, "xoptions": {"utf8": True}, "encoding": "utf-8"}, False),
    ({"xoptions": {"utf8": "0"}}, False),
    ({"name": "nt", "encoding": "mbcs"}, False),
    ({"executable": ""}, False),
], ids=["posix-latin1", "posix-ascii", "posix-utf8", "posix-utf8-other-spelling", "utf8-mode-on",
        "utf8-mode-refused", "windows", "no-interpreter-path"])
def test_only_a_posix_process_whose_paths_are_not_utf8_restarts(monkeypatch, state, restarts):
    execs = _stand_ins(monkeypatch, **state)

    cli._restart_in_utf8_mode()

    assert bool(execs) is restarts


@pytest.mark.parametrize("encoding", ["iso8859-1", "ascii"])
@pytest.mark.parametrize("tail", [
    [b"-m", b"crapkit", b"rescore", b"pkg/caf\xc3\xa9.py"],
    [b"/venv/bin/crapkit", b"claude-hook"],
    [b"-I", b"-W", b"error", b"-m", b"crapkit", b"worklist"],
    [b"-m", b"crapkit", b"rescore", b"pkg/caf\xe9.py"],
], ids=["module", "console-script", "interpreter-options", "argument-not-utf8"])
def test_the_restart_passes_on_the_bytes_the_shell_passed(monkeypatch, encoding, tail):
    """The same interpreter options, the same script or module, and each
    argument as its original bytes, after `-X utf8`."""
    execs = _stand_ins(monkeypatch, encoding=encoding, argv=(PYTHON.encode(), *tail))

    cli._restart_in_utf8_mode()

    assert execs == [(PYTHON, [PYTHON, "-X", "utf8", *tail])]


@pytest.mark.parametrize("argv, restarts", [(None, True), (["worklist"], False)],
                         ids=["from-the-command-line", "argv-given"])
def test_only_a_command_line_start_restarts(monkeypatch, argv, restarts):
    """A caller that hands main its own argv (the test suite's in-process
    runner, the MCP server's relay) runs where it is."""
    calls = []
    monkeypatch.setattr(cli, "_restart_in_utf8_mode", lambda: calls.append(True))
    monkeypatch.setattr("crapkit.cli.parser.main", lambda given: 0)

    assert cli.main(argv) == 0
    assert bool(calls) is restarts
