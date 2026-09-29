"""The small corpus's coverage counts, read from its recorded artifacts with json.load.

No crapkit: the counts come from the producers' own files.

- coverage.py JSON (format 3): every function region of every file, keyed by
  its `start_line`, with the region's own summary counts (num_branches and
  covered_branches, num_statements and covered_lines), whether any of its
  lines ran, and whether coverage.py excluded every statement in it (its lines
  are all excluded_lines, as `# pragma: no cover` on the def leaves them).
- istanbul JSON (coverage-final.json): every fnMap entry, its call count, and
  the branch arms and statements that count against it, by the rule
  docs/lanes.md's istanbul key table states. A function's span runs from
  decl.start to loc.end (else the end of the start line), and its body from
  loc.start (else decl.start). A branch counts against the innermost function
  whose span holds its loc.start, a statement against the innermost one whose
  body holds its start. Positions compare the line, then the column, and a
  missing column stands for the whole line: the first column at a start, the
  last at an end. The innermost is the one over the fewest lines, then the one
  that starts later, then the one fnMap lists first.

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
    excluded: bool = False

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
                  bool(region.get("executed_lines")),
                  excluded=bool(region.get("excluded_lines")) and not lines)


def coveragepy(artifact: Path) -> list[Counts]:
    files = json.loads(artifact.read_text(encoding="utf-8"))["files"]
    return [_region(path, name, region) for path, data in files.items()
            for name, region in data["functions"].items() if name]


# --- istanbul ------------------------------------------------------------------------

LAST = float("inf")


def _point(point: dict | None, missing: float) -> tuple:
    """(line, column); a missing column stands for the whole line."""
    if not point or point.get("line") is None:
        return (0, 0)
    column = point.get("column")
    return (point["line"], missing if column is None else column)


@dataclass(frozen=True)
class _Span:
    key: str
    name: str
    start: tuple
    end: tuple
    body: tuple


def _spans(data: dict) -> list[_Span]:
    return [_span(key, entry) for key, entry in data["fnMap"].items()]


def _span(key: str, entry: dict) -> _Span:
    """One fnMap entry: it opens at decl.start, its body at loc.start (else
    decl.start), and it ends at loc.end (else the end of its first line)."""
    start = _point(entry["decl"]["start"], 0)
    loc = entry.get("loc") or {}
    end = _point(loc.get("end"), LAST)
    body = _point(loc.get("start"), 0)
    return _Span(key, entry.get("name") or "(anonymous)", start,
                 end if end[0] else (start[0], LAST), body if body[0] else start)


def innermost(spans: list[_Span], at: tuple, opens=lambda span: span.start) -> _Span | None:
    """The innermost span holding `at` from where `opens` says it opens: over the
    fewest lines, then the later start, then the first listed."""
    holding = [span for span in spans if opens(span) <= at <= span.end]
    return min(holding, key=lambda span: (span.end[0] - span.start[0],
                                          tuple(-part for part in span.start)), default=None)


def _attributed(spans: list[_Span], starts: dict[str, tuple], opens) -> dict[str, list[str]]:
    """{fnMap key: [entry ids]} for entries whose start the function holds innermost."""
    owned: dict[str, list[str]] = defaultdict(list)
    for entry, at in starts.items():
        owner = innermost(spans, at, opens)
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
    branch_starts = {key: _point(branch["loc"]["start"], 0)
                     for key, branch in data["branchMap"].items()}
    statement_starts = {key: _point(statement["start"], 0)
                        for key, statement in data["statementMap"].items()}
    branches = _attributed(spans, branch_starts, lambda span: span.start)
    statements = _attributed(spans, statement_starts, lambda span: span.body)
    starts = [span.start[0] for span in spans]
    return [Counts(path, span.name, span.start[0], _last_line(span),
                   *_arms(data, branches[span.key]), *_statements(data, statements[span.key]),
                   data["f"][span.key] > 0, starts.count(span.start[0]) > 1)
            for span in spans]


def _last_line(span: _Span) -> int:
    return int(span.end[0])


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
