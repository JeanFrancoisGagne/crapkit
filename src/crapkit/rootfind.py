"""Where crapkit.toml is, found the way git finds `.git`: upward from where the
caller stands (ADR 0002).

Stdlib only, on purpose. The advisory hook calls this on every edit in every
repository on the machine, measured or not, and its warm-path budget pins a
path that imports nothing of crapkit's beyond this before it learns there is
no configuration to read. The commands and the MCP server call the same
function, so one rule decides which configuration is in use everywhere.
"""
from __future__ import annotations

from pathlib import Path

CONFIG_NAME = "crapkit.toml"

# Deeper than any real checkout, bounded so a pathological path cannot turn the
# walk into a filesystem scan.
MAX_LEVELS = 64


def find_root(start: Path) -> Path | None:
    """The nearest directory at or above `start` holding crapkit.toml, or None.

    Nearest wins, so a nested configuration shadows an ancestor's. A `.git`
    entry without one stops the walk: a linked worktree carries `.git` as a
    FILE and a nested repository as a directory, and walking past either would
    lend that tree a parent's configuration and the parent's store. Each level
    costs one or two stats.
    """
    for directory in [start, *start.parents][:MAX_LEVELS]:
        if (directory / CONFIG_NAME).is_file():
            return directory
        if (directory / ".git").exists():
            return None
    return None
