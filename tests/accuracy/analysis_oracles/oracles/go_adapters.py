"""gocyclo 0.6.0, gocognit 1.2.1 and revive 1.17.0 max-control-nesting, read per function.

Each reader takes a directory and the file paths under it and answers
{(path, start line): value}. gocyclo and gocognit start one process for the
whole list and print one line per function, `<value> <package> <function>
<file>:<line>:<column>`, with -over 0 (gocyclo) and -over -1 (gocognit) so
that every function prints. revive prints `control flow nesting exceeds
<limit>` at each statement past its configured limit: the number is the
limit, never the depth reached (AO-REVIVE-SWEEP). So revive_depths() runs it
at limit 0, 1, 2, ... over the files that still report, until none does, and
a function's depth is one more than the largest limit a report inside its
span passes: a set whose deepest function nests d levels takes d + 1 revive
processes.

The transforms from each tool's count to crapkit's documented one live in
test_go_oracles.py, one rulings row each. No crapkit import.
"""
from __future__ import annotations

from pathlib import Path
import re

import hang_guard

FUNCTION_LINE = re.compile(r"^(\d+) \S+ \S+ (.+):(\d+):\d+$")
REVIVE_LINE = re.compile(r"^(.+):(\d+):\d+: control flow nesting exceeds (\d+)$")
# No Go function nests this deep; a sweep that reaches it has a revive that never stops.
CEILING = 64


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


def revive_lines(root: Path, paths: list, limit: int) -> list:
    """Each statement revive says passes `limit`, as (path, line, the number its
    report names)."""
    config = root / f"revive-{limit}.toml"
    config.write_text(f"[rule.max-control-nesting]\narguments = [{limit}]\n", encoding="utf-8")
    done = hang_guard.run(["revive", "-config", str(config), *paths], cwd=root, text=True,
                          encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stdout + done.stderr
    matches = [REVIVE_LINE.match(line) for line in done.stdout.splitlines()]
    return [(match[1], int(match[2]), int(match[3])) for match in matches if match]


def revive_reports(root: Path, paths: list, limit: int) -> set:
    """(path, line) of each statement revive says passes `limit`."""
    found = revive_lines(root, paths, limit)
    assert {named for _, _, named in found} <= {limit}, found
    return {(path, line) for path, line, _ in found}


def _deepen(depths: dict, spans: dict, reports: set, depth: int) -> None:
    for report in reports:
        owner = innermost(spans, *report)
        if owner is not None:
            depths[owner] = max(depths[owner], depth)


def revive_depths(root: Path, paths: list, spans: dict) -> dict:
    """{(path, start): depth} for each function in `spans` ({(path, start): end}).
    A report belongs to the innermost function whose lines hold it."""
    depths, limit, left = dict.fromkeys(spans, 0), 0, list(paths)
    while left:
        assert limit < CEILING, f"revive still reports past limit {CEILING - 1} in {left}"
        reports = revive_reports(root, left, limit)
        _deepen(depths, spans, reports, limit + 1)
        left = sorted({path for path, _ in reports})
        limit += 1
    return depths


def innermost(spans: dict, path: str, line: int):
    holding = [key for key, end in spans.items() if key[0] == path and key[1] <= line <= end]
    return max(holding, key=lambda key: key[1], default=None)
