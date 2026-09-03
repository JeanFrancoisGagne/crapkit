"""Failure classes with distinct exit codes. A broken pipeline never renders as a healthy zero.

`kind` is the class's name on the wire: under `--json` an error that escapes a
command still prints one object on stdout, `{"error": {"exit", "kind",
"message"}, "schema": 1}`, so a wrapper reads the sentence that names the fix
instead of an empty stream.
"""
from __future__ import annotations


class CrapkitError(Exception):
    """A state the command cannot answer from: no run, no matching function,
    no open claim."""
    exit_code = 1
    kind = "state"


class ConfigError(CrapkitError):
    exit_code = 3
    kind = "config"


class GitError(CrapkitError):
    exit_code = 4
    kind = "git"


class ToolError(CrapkitError):
    exit_code = 5
    kind = "tool"
