"""The small corpus's coverage counts, read from its recorded artifacts with json.load.

No crapkit: the counts come from the producers' own files.

- coverage.py JSON (format 3): every function region of every file, keyed by
  its `start_line`, with the region's own summary counts (num_branches and
  covered_branches, num_statements and covered_lines) and whether any of its
  lines ran.
- istanbul JSON (coverage-final.json): every fnMap entry, its span from
  decl.start.line to loc.end.line (docs/lanes.md, the istanbul key table), its
  call count, and the branch arms and statements that count against it. A
  branch counts against the innermost function whose span holds the branch's
  loc.start.line, which the same table states; a statement is attributed the
  same way here, which that table does not say (it says only "in its span").

A function two entries share a start line with is marked `shared`: coverage
cannot say whose counts are whose.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path


@dataclass(frozen=True)
class Counts:
    path: str
    name: str
    start: int
    end: int
    branches_covered: int
    branches_total: int
    statements_covered: int
    statements_total: int
    invoked: bool
    shared: bool = False

    def ratio(self) -> Fraction:
        """Branches when there are any, then statements, then invoked or not
        (README.md, "cov is branch coverage inside the function's span")."""
        if self.branches_total:
            return Fraction(self.branches_covered, self.branches_total)
        if self.statements_total:
            return Fraction(self.statements_covered, self.statements_total)
        return Fraction(int(self.invoked))


# --- coverage.py -----------------------------------------------------------------

def _region(path: str, name: str, region: dict) -> Counts:
    summary = region["summary"]
    lines = region.get("executed_lines", []) + region.get("missing_lines", [])
    start = region.get("start_line") or min(lines, default=0)
    return Counts(path, name, start, max(lines, default=start),
                  summary.get("covered_branches", 0), summary.get("num_branches", 0),
                  summary["covered_lines"], summary["num_statements"],
                  bool(region.get("executed_lines")))


def coveragepy(artifact: Path) -> list[Counts]:
    files = json.loads(artifact.read_text(encoding="utf-8"))["files"]
    return [_region(path, name, region) for path, data in files.items()
            for name, region in data["functions"].items() if name]


# --- istanbul ------------------------------------------------------------------------

@dataclass(frozen=True)
class _Span:
    key: str
    name: str
    start: int
    end: int


def _spans(data: dict) -> list[_Span]:
    spans = []
    for key, entry in data["fnMap"].items():
        start = entry["decl"]["start"]["line"]
        end = (entry.get("loc") or {}).get("end", {}).get("line") or start
        spans.append(_Span(key, entry.get("name") or "(anonymous)", start, end))
    return spans


def innermost(spans: list[_Span], line: int) -> _Span | None:
    """The deepest span holding `line`: the latest start, then the earliest end."""
    holding = [span for span in spans if span.start <= line <= span.end]
    return max(holding, key=lambda span: (span.start, -span.end), default=None)


def _attributed(spans: list[_Span], lines: dict[str, int]) -> dict[str, list[str]]:
    """{fnMap key: [entry ids]} for entries whose line the function holds innermost."""
    owned: dict[str, list[str]] = defaultdict(list)
    for entry, line in lines.items():
        owner = innermost(spans, line)
        if owner is not None:
            owned[owner.key].append(entry)
    return owned


def _arms(data: dict, ids: list[str]) -> tuple[int, int]:
    arms = [count for entry in ids for count in data["b"][entry]]
    return sum(1 for count in arms if count > 0), len(arms)


def _statements(data: dict, ids: list[str]) -> tuple[int, int]:
    return sum(1 for entry in ids if data["s"][entry] > 0), len(ids)


def _file_counts(path: str, data: dict) -> list[Counts]:
    spans = _spans(data)
    branch_lines = {key: branch["loc"]["start"]["line"] for key, branch in data["branchMap"].items()}
    statement_lines = {key: statement["start"]["line"]
                       for key, statement in data["statementMap"].items()}
    branches = _attributed(spans, branch_lines)
    statements = _attributed(spans, statement_lines)
    starts = [span.start for span in spans]
    return [Counts(path, span.name, span.start, span.end, *_arms(data, branches[span.key]),
                   *_statements(data, statements[span.key]), data["f"][span.key] > 0,
                   starts.count(span.start) > 1)
            for span in spans]


def istanbul(artifact: Path) -> list[Counts]:
    files = json.loads(artifact.read_text(encoding="utf-8"))
    return [counts for path, data in files.items() for counts in _file_counts(path, data)]


def table(recorded: Path) -> dict[tuple[str, int], list[Counts]]:
    """{(path, start line): counts} over every artifact a small-corpus lane copies."""
    rows = coveragepy(recorded / "py.json") + istanbul(recorded / "js.json")
    found: dict[tuple[str, int], list[Counts]] = defaultdict(list)
    for row in rows:
        found[(row.path, row.start)].append(row)
    return dict(found)
