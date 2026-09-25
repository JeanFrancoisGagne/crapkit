"""Per-function coverage counts read straight off an artifact, with json.load and no crapkit.

Every row is one function the artifact names: its path and start line, its branch
and statement counts, whether it was called, and the arms and statement lines
behind the counts. Two readers:

- coverage.py: one row per entry of a file's "functions" regions (coverage.py
  7.6 and later), split by coverage.py itself. The branch arms are the region's
  executed and missing arcs, the statements its executed and missing lines.
  start is the region's start_line, or its first line when an older report
  (before 7.13.1) has none.
- istanbul: one row per fnMap entry. Each branchMap and statementMap counter goes
  to one function, by one of two rules:
    `line`: the innermost function whose span [decl.start.line, loc.end.line]
      holds the counter's start line (docs/lanes.md#what-the-istanbul-parser-reads).
      A tie on span length goes to the later start, then the one listed first.
    `position`: the innermost function whose range, decl.start to loc.end in
      (line, column), holds the counter's range; a counter whose range is the
      function's own (the `const f = () => ...` declaration that runs at import)
      stays outside it. This is what ran inside which body.
  A negative branch count, which @vitest/coverage-v8 derives by subtraction and
  can underflow, reads as not taken.

ratio() is the README's coverage term: branches, else statements, else called.
table() reads every recorded artifact of a corpus through its crapkit.toml
lanes, keyed (repository path, start line).
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
import tomllib

END = math.inf


@dataclass(frozen=True)
class Counts:
    path: str
    name: str
    start: int
    end: int
    arms: tuple[str, ...]
    arms_taken: tuple[str, ...]
    stmts: tuple[int, ...]
    stmts_run: tuple[int, ...]
    invoked: bool

    @property
    def covered(self) -> int:
        return len(self.arms_taken)

    @property
    def total(self) -> int:
        return len(self.arms)


def ratio(counts: Counts) -> Fraction:
    """README.md: branch coverage inside the span; with no branches, statement
    coverage; with no statements, called or not."""
    if counts.arms:
        return Fraction(len(counts.arms_taken), len(counts.arms))
    if counts.stmts:
        return Fraction(len(counts.stmts_run), len(counts.stmts))
    return Fraction(int(counts.invoked))


# --- coverage.py ------------------------------------------------------------------------

def _arc(pair) -> str:
    return f"{pair[0]}>{pair[1]}"


def _region_start(region: dict) -> int:
    lines = list(region.get("executed_lines", ())) + list(region.get("missing_lines", ()))
    return region.get("start_line") or (min(lines) if lines else 0)


def _region(path: str, name: str, region: dict) -> Counts:
    """An excluded line is no statement, even where a report before 7.16 also
    lists it as executed."""
    excluded = set(region.get("excluded_lines", ()))
    executed = tuple(sorted(set(region.get("executed_lines", ())) - excluded))
    lines = tuple(sorted((set(executed) | set(region.get("missing_lines", ()))) - excluded))
    taken = tuple(sorted(map(_arc, region.get("executed_branches", ()))))
    arms = tuple(sorted(taken + tuple(map(_arc, region.get("missing_branches", ())))))
    start = _region_start(region)
    return Counts(path, name, start, max(lines, default=start), arms, taken, lines, executed,
                  bool(executed))


def coveragepy_rows(report: dict, prefix: str = "") -> list[Counts]:
    """One row per named region of every file; the module bucket "" is not a function."""
    rows = []
    for key, data in report.get("files", {}).items():
        path = prefix + key.replace("\\", "/")
        regions = data.get("functions") or {}
        rows += [_region(path, name, region) for name, region in regions.items() if name]
    return rows


# --- istanbul ---------------------------------------------------------------------------

def _position(point: dict | None, missing: float) -> tuple[float, float]:
    if not point or point.get("line") is None:
        return (missing, missing)
    column = point.get("column")
    return (point["line"], missing if column is None else column)


@dataclass(frozen=True)
class _Function:
    index: str
    name: str
    start: tuple
    end: tuple

    @property
    def lines(self) -> tuple[int, float]:
        return self.start[0], self.end[0]

    @property
    def last_line(self) -> int:
        return int(self.start[0] if self.end[0] == END else self.end[0])


def _functions(data: dict) -> list[_Function]:
    found = []
    for index, entry in data.get("fnMap", {}).items():
        start = _position(entry["decl"]["start"], 0)
        end = _position((entry.get("loc") or {}).get("end"), END)
        end = end if end[0] != END else (start[0], END)
        found.append(_Function(index, entry.get("name") or "(anonymous)", start, end))
    return found


def _by_line(functions: list[_Function], start: tuple, end: tuple) -> _Function | None:
    """The shortest span holding the line; on a tie the one that starts later,
    then the one listed first."""
    holders = [fn for fn in functions if fn.lines[0] <= start[0] <= fn.lines[1]]
    return min(holders, key=lambda fn: (fn.lines[1] - fn.lines[0], -fn.lines[0]), default=None)


def _holds(fn: _Function, start: tuple, end: tuple) -> bool:
    inside = fn.start <= start and end <= fn.end
    return inside and (start, end) != (fn.start, fn.end)


def _by_position(functions: list[_Function], start: tuple, end: tuple) -> _Function | None:
    holders = [fn for fn in functions if _holds(fn, start, end)]
    return max(holders, key=lambda fn: (fn.start, _negated(fn.end)), default=None)


def _negated(point: tuple) -> tuple:
    return tuple(-value for value in point)


OWNERS = {"line": _by_line, "position": _by_position}


def _span(loc: dict) -> tuple[tuple, tuple]:
    return _position(loc.get("start"), 0), _position(loc.get("end"), END)


def _branch_arms(data: dict):
    """(start, end, arm label, taken) per branch location."""
    for index, branch in data.get("branchMap", {}).items():
        start, end = _span(branch.get("loc") or {})
        hits = data.get("b", {}).get(index, [])
        label = f"{branch.get('type')}@{start[0]}"
        for arm, count in enumerate(hits):
            yield start, end, f"{label}:{arm}", count > 0


def _statements(data: dict):
    """(start, end, line, ran) per statement."""
    for index, statement in data.get("statementMap", {}).items():
        start, end = _span(statement)
        yield start, end, start[0], data.get("s", {}).get(index, 0) > 0


def _assign(functions, items, owner) -> dict[str, list]:
    owned: dict[str, list] = {fn.index: [] for fn in functions}
    for start, end, label, taken in items:
        fn = owner(functions, start, end)
        if fn is not None:
            owned[fn.index].append((label, taken))
    return owned


def _all_and_hit(owned: list) -> tuple[tuple, tuple]:
    """(every item, the items taken or run), each sorted."""
    return (tuple(sorted(item for item, _ in owned)),
            tuple(sorted(item for item, hit in owned if hit)))


def _istanbul_file(path: str, data: dict, rule: str) -> list[Counts]:
    functions = _functions(data)
    owner = OWNERS[rule]
    arms = _assign(functions, _branch_arms(data), owner)
    stmts = _assign(functions, _statements(data), owner)
    calls = data.get("f", {})
    return [Counts(path, fn.name, int(fn.start[0]), fn.last_line, *_all_and_hit(arms[fn.index]),
                   *_all_and_hit(stmts[fn.index]), calls.get(fn.index, 0) > 0)
            for fn in functions]


def relative_key(key: str, root: str = "") -> str:
    """An artifact key with the checkout root cut off, forward slashes."""
    norm = key.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/") + "/" if root else ""
    return norm[len(base):] if base and norm.startswith(base) else norm


def istanbul_rows(artifact: dict, rule: str = "line", root: str = "") -> list[Counts]:
    return [row for key, data in artifact.items()
            for row in _istanbul_file(relative_key(key, root), data, rule)]


# --- a corpus's recorded artifacts ---------------------------------------------------------

def _copied(command: str) -> dict[str, str]:
    """{artifact path: recorded source} from a kit.repos.copy_command lane: the
    words after the quoted program are (source, destination) pairs."""
    words = command.rpartition('"')[2].split()
    return dict(zip(words[1::2], words[0::2]))


def _lane_rows(corpus: Path, lane: dict) -> list[Counts]:
    source = _copied(lane.get("command", "")).get(lane.get("artifact", ""))
    if source is None:
        return []
    artifact = json.loads((corpus / source).read_bytes())
    if lane.get("parser") == "coveragepy":
        prefix = lane.get("path_prefix", "")
        return coveragepy_rows(artifact, prefix.rstrip("/") + "/" if prefix else "")
    return istanbul_rows(artifact, "line")


def table(corpus: Path) -> dict[tuple[str, int], list[Counts]]:
    """{(path, start): rows} over every lane of the corpus's crapkit.toml whose
    command copies a recorded artifact into place."""
    config = tomllib.loads((corpus / "crapkit.toml").read_text(encoding="utf-8"))
    found: dict[tuple[str, int], list[Counts]] = {}
    for lane in config.get("lane", []):
        for row in _lane_rows(corpus, lane):
            found.setdefault((row.path, row.start), []).append(row)
    return found
