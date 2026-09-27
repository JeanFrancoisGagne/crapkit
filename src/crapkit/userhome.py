"""The home directory of the user a process runs as: the environment first, the
operating system second.

The worker budget, the measurement locks and the Claude Code plugin cache all
live under it. Path.home() reads USERPROFILE, or HOMEDRIVE with HOMEPATH, on
Windows and raises when a process starts with none of them: a client that
builds a server's environment from an allowlist, a service, a scheduled task.
HOME does not count there. Windows still reports the profile folder of the
process's user, which is the folder USERPROFILE names in every other process
that user runs, so the caches and locks stay shared. On POSIX, Path.home()
already reads the password database once HOME is gone.
"""
from __future__ import annotations

import os
from pathlib import Path

from .errors import ToolError

_CSIDL_PROFILE = 0x28
_MAX_PATH = 260


def _windows_profile() -> str:
    """The profile folder the Windows shell reports for this process's user, or
    "" when it reports none."""
    import ctypes
    folder = ctypes.create_unicode_buffer(_MAX_PATH)
    failed = ctypes.windll.shell32.SHGetFolderPathW(None, _CSIDL_PROFILE, None, 0, folder)
    return "" if failed else folder.value


def _system_home() -> str:
    return _windows_profile() if os.name == "nt" else ""


def user_home() -> Path:
    """Path.home(), else the profile folder Windows reports.

    Raises ToolError naming the variable to set when neither answers.
    """
    try:
        return Path.home()
    except RuntimeError:
        found = _system_home()
    if found:
        return Path(found)
    variable = "USERPROFILE" if os.name == "nt" else "HOME"
    raise ToolError(f"no home directory: {variable} is unset and the operating system names "
                    f"none for this user; set {variable} to the home this user's other "
                    "processes use, where crapkit keeps its worker slots and measurement locks")
