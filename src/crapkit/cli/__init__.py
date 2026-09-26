"""Public CLI process entry point. Helpers live in their command-family modules."""
from __future__ import annotations

import codecs
import os
import sys

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Load the parser only when a caller starts the CLI. A process started
    from the command line first makes sure it spells paths in UTF-8
    (_restart_in_utf8_mode)."""
    if argv is None:
        _restart_in_utf8_mode()
    from .parser import main as dispatch

    return dispatch(argv)


def _restart_in_utf8_mode() -> None:
    """Start this command again with `-X utf8` when the POSIX locale would
    spell repo paths in another encoding.

    git names a file in the bytes it was created with, and crapkit keys it as
    that UTF-8 text. Python hands a str back to the OS through the filesystem
    encoding, which on POSIX is the locale's: under en_US.ISO-8859-1,
    `pkg/café.py` went back as b"pkg/caf\\xe9.py", a file that does not exist,
    so coverage skipped it as missing and claude-hook said nothing about it.
    In UTF-8 mode every open, stat and argument spells UTF-8.

    The flag, not PYTHONUTF8, so lane and mutation children keep the
    environment the user gave them. The new process gets the interpreter
    options and the arguments as the bytes the shell passed, and nothing has
    read stdin yet, so a hook payload or an MCP frame reaches it whole. It runs
    in UTF-8 mode and never restarts again; an explicit `-X utf8=0` is left
    alone."""
    if _needs_utf8_mode() and sys.executable:
        os.execv(sys.executable, [sys.executable, "-X", "utf8", *map(os.fsencode, sys.orig_argv[1:])])


def _needs_utf8_mode() -> bool:
    """POSIX, UTF-8 mode off and not refused, and a filesystem encoding that is
    not UTF-8. Windows and macOS always name files in UTF-8."""
    if os.name != "posix" or sys.flags.utf8_mode or "utf8" in sys._xoptions:
        return False
    return codecs.lookup(sys.getfilesystemencoding()).name != "utf-8"
