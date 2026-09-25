"""gocyclo 0.6.0, gocognit 1.2.1 and revive 1.17.0 max-control-nesting, read per function.

Each reader takes a directory and the file paths under it, starts one process
for the whole list, and answers {(path, start line): value}. gocyclo and
gocognit print one line per function, `<value> <package> <function>
<file>:<line>:<column>`, with -over 0 (gocyclo) and -over -1 (gocognit) so
that every function prints. revive names only the statements past a nesting
limit, so revive_depths() runs it once per limit 0 to TOP-1 and a function's
depth is one more than the largest limit a report inside its span passes.

The transforms from each tool's count to crapkit's documented one live in
test_go_oracles.py, one rulings row each. No crapkit import.
"""
from __future__ import annotations

from pathlib import Path
import re

import hang_guard

FUNCTION_LINE = re.compile(r"^(\d+) \S+ \S+ (.+):(\d+):\d+$")
REVIVE_LINE = re.compile(r"^(.+):(\d+):\d+: control flow nesting exceeds (\d+)$")
TOP = 8


def _function_lines(argv: list, root: Path) -> dict:
    done = hang_guard.run(argv, cwd=root, text=True, encoding="utf-8", errors="replace")
    found = {}
    for line in done.stdout.splitlines():
        match = FUNCTION_LINE.match(line)
        if match:
            found[(match[2], int(match[3]))] = int(match[1])
    assert found, done.stdout + done.stderr
    return found


def gocyclo(root: Path, paths: list) -> dict:
    return _function_lines(["gocyclo", "-over", "0", *paths], root)


def gocognit(root: Path, paths: list) -> dict:
    return _function_lines(["gocognit", "-over", "-1", *paths], root)


def revive_reports(root: Path, paths: list, limit: int) -> set:
    """(path, line) of each statement revive says passes `limit`."""
    config = root / f"revive-{limit}.toml"
    config.write_text(f"[rule.max-control-nesting]\narguments = [{limit}]\n", encoding="utf-8")
    done = hang_guard.run(["revive", "-config", str(config), *paths], cwd=root, text=True,
                          encoding="utf-8", errors="replace")
    matches = [REVIVE_LINE.match(line) for line in done.stdout.splitlines()]
    return {(match[1], int(match[2])) for match in matches if match}


def revive_depths(root: Path, paths: list, spans: dict) -> dict:
    """{(path, start): depth} for each function in `spans` ({(path, start): end}).
    A report belongs to the innermost function whose lines hold it."""
    depths = dict.fromkeys(spans, 0)
    for limit in range(TOP):
        for report in revive_reports(root, paths, limit):
            owner = innermost(spans, *report)
            if owner is not None:
                depths[owner] = max(depths[owner], limit + 1)
    return depths


def innermost(spans: dict, path: str, line: int):
    holding = [key for key, end in spans.items() if key[0] == path and key[1] <= line <= end]
    return max(holding, key=lambda key: key[1], default=None)
