"""Failure classes with distinct exit codes. A broken pipeline never renders as a healthy zero.

`kind` is the class's name on the wire: under `--json` an error that escapes a
command still prints one object on stdout, `{"error": {"exit", "kind",
"message"}, "schema": 1}`, so a wrapper reads the sentence that names the fix
instead of an empty stream. A refusal that names files adds them beside those
three (`json_fields`).
"""
from __future__ import annotations


class CrapkitError(Exception):
    """A state the command cannot answer from: no run, no matching function,
    no open claim."""
    exit_code = 1
    kind = "state"

    def json_fields(self) -> dict:
        """What the --json error object carries beside exit, kind and message."""
        return {}


class ConfigError(CrapkitError):
    exit_code = 3
    kind = "config"


# Each `unread_files` item's reason for a name crapkit cannot read.
UNREAD_NAME_REASON = ("its name is not UTF-8, and crapkit reads every path as UTF-8: "
                      "rename it (git mv) to a UTF-8 name")


class UnreadableNameError(ConfigError):
    r"""Exit 3 for files whose names are not UTF-8, each name as git gives it
    (a lone surrogate per such byte). The error object lists them in
    `unread_files`, in the item shape every gate verdict lists unread files in,
    {path, reason, dirty}: `path` spells each such byte `\xNN`, as the stderr
    line does, and `dirty` is verify's, true for a name in `dirty`. The command
    that knows the working tree fills `dirty` (`with_dirty`); an adapter that
    speaks another protocol (check_gate) returns the refusal as that verdict."""

    def __init__(self, message: str, names: list[str], dirty: frozenset[str] = frozenset()):
        super().__init__(message)
        self.names = tuple(names)
        self.dirty = frozenset(dirty)

    def with_dirty(self, dirty: frozenset[str]) -> UnreadableNameError:
        """The same refusal, `dirty` naming the files with uncommitted edits or
        that git does not track."""
        return UnreadableNameError(str(self), list(self.names), dirty)

    def json_fields(self) -> dict:
        from .gitpaths import shown

        return {"unread_files": [{"path": shown(name), "reason": UNREAD_NAME_REASON,
                                  "dirty": name in self.dirty} for name in self.names]}


class GitError(CrapkitError):
    exit_code = 4
    kind = "git"


class ToolError(CrapkitError):
    exit_code = 5
    kind = "tool"
