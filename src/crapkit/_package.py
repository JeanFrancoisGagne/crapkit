"""Whether the package on disk is still the one this process imported.

`pip install -U crapkit` replaces these files under a running process, which
keeps the old code in memory. A module that process imports later comes from
the new release, and the two sides disagree on signatures: the MCP server
answered JSON-RPC -32603 `TypeError: _operation() takes 2 positional
arguments but 3 were given`, and `crapkit watch` died with a traceback at its
first rescore. A long-running command imports this module when it starts,
never lazily, because after an upgrade a lazy import would read the new file.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import __version__

_INIT = Path(__file__).with_name("__init__.py")
_VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"', re.MULTILINE)


def installed_version() -> str | None:
    """The version the package directory holds now, or None when it cannot be
    read (a zip import, a directory mid-install): no evidence of an upgrade."""
    try:
        found = _VERSION_LINE.search(_INIT.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None
    return found[1] if found else None


def upgraded_to() -> str | None:
    """The version on disk when it is not the one this process loaded, else None."""
    installed = installed_version()
    return None if installed in (None, __version__) else installed
