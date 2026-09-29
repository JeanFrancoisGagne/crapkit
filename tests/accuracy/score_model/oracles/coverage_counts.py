"""Per-function coverage counts read straight off a lane's artifact with json.load.

The counts are (branches covered, total), (statements covered, total) and
whether the function was called, keyed by (path, start line). Two producers:

- coverage.py JSON (format 3, 7.13.1 and later): each file's "functions"
  member carries coverage.py's own per-function summary and start_line
  (https://coverage.readthedocs.io/en/7.16.1/cmd.html#cmd-json). The "" key is
  the module-level bucket, not a function.
- istanbul coverage-final.json: fnMap, f, branchMap, b, statementMap and s
  (https://github.com/istanbuljs/istanbuljs/blob/main/docs/raw-output.md).
  crapkit's docs/lanes.md:222-238 fix the attribution: a function's span runs
  from decl.start.line to loc.end.line, and each branch and statement counts
  against the innermost function whose span holds its start line. Innermost
  means the latest start, then the shortest span.

No crapkit import: the join test compares these counts with what crapkit
scored, so they must not come from crapkit's readers.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class Counts:
    path: str
    start: int
    end: int
    branches: tuple[int, int]
    statements: tuple[int, int]
    invoked: bool


def _coveragepy_function(path: str, entry: dict) -> Counts:
    summary = entry["summary"]
    lines = [*entry.get("executed_lines", ()), *entry.get("missing_lines", ())]
    start = entry["start_line"]
    return Counts(path, start, max(lines, default=start),
                  (summary["covered_branches"], summary["num_branches"]),
                  (summary["covered_lines"], summary["num_statements"]),
                  summary["covered_lines"] > 0)


def coveragepy(report: dict) -> list[Counts]:
    return [_coveragepy_function(path, entry)
            for path, data in report["files"].items()
            for name, entry in data.get("functions", {}).items() if name]


def _span(fn: dict) -> tuple[int, int]:
    start = fn["decl"]["start"]["line"]
    end = (fn.get("loc", {}).get("end") or {}).get("line") or start
    return start, end


def _innermost(spans: dict, line: int):
    holders = [(start, -end, key) for key, (start, end) in spans.items() if start <= line <= end]
    return max(holders)[2] if holders else None


def _add(found: dict, owner, hits: list[int]) -> None:
    if owner is not None:
        found[owner][0] += sum(1 for hit in hits if hit > 0)
        found[owner][1] += len(hits)


def _tally(spans: dict, items: list[tuple[int, list[int]]]) -> dict:
    """(covered, total) per function: every hit count at a line goes to the
    innermost function holding it."""
    found = {key: [0, 0] for key in spans}
    for line, hits in items:
        _add(found, _innermost(spans, line), hits)
    return found


def _istanbul_file(path: str, data: dict) -> list[Counts]:
    spans = {key: _span(fn) for key, fn in data["fnMap"].items()}
    branches = _tally(spans, [(data["branchMap"][key]["loc"]["start"]["line"], hits)
                              for key, hits in data["b"].items()])
    statements = _tally(spans, [(data["statementMap"][key]["start"]["line"], [hits])
                                for key, hits in data["s"].items()])
    return [Counts(path, start, end, tuple(branches[key]), tuple(statements[key]),
                   data["f"].get(key, 0) > 0)
            for key, (start, end) in spans.items()]


def istanbul(report: dict) -> list[Counts]:
    return [counts for path, data in report.items() for counts in _istanbul_file(path, data)]


READERS = {"coveragepy": coveragepy, "istanbul": istanbul}


def read(parser: str, artifact: Path) -> list[Counts]:
    """Every function's counts in one artifact; an unknown parser is an error."""
    if parser not in READERS:
        raise LookupError(f"no counts reader for lane parser {parser!r}; add one to "
                          f"{Path(__file__).name}")
    return READERS[parser](json.loads(artifact.read_bytes()))
