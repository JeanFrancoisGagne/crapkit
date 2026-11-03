"""Per-function coverage counts read straight off an artifact, with json.load and no crapkit.

Every row is one function the artifact names: its path and start line, its branch
and statement counts, whether it was called, and the arms and statement lines
behind the counts. Three readers:

- coverage.py: one row per entry of a file's "functions" regions (coverage.py
  7.6 and later), split by coverage.py itself. The branch arms are the region's
  executed and missing arcs, the statements its executed and missing lines.
  start is the region's start_line, or its first line when an older report
  (before 7.13.1) has none.
- istanbul: one row per fnMap entry. Each branchMap and statementMap counter goes
  to one function, by one of two rules:
    `line`: the rule docs/lanes.md#what-the-istanbul-parser-reads states. A
      function's span runs from decl.start to loc.end, and its body from
      loc.start (else decl.start) to loc.end. A branch goes to the innermost
      function whose span holds its loc.start, a statement to the innermost one
      whose body holds its start. Positions compare the line, then the column,
      and a missing column stands for the whole line: the first column at a
      start, the last at an end. The innermost is the one over the fewest lines,
      then the one that starts later, then the one listed first.
    `position`: the innermost function whose range, decl.start to loc.end in
      (line, column), holds the counter's range; a counter whose range is the
      function's own (the `const f = () => ...` declaration that runs at import)
      stays outside it. This is what ran inside which body.
  A negative branch count, which @vitest/coverage-v8 derives by subtraction and
  can underflow, reads as not taken.
- lcov: lcov_rows gives each span the caller names (path, start, end) the DA
  lines and BRDA arms on the lines it holds and no inner span holds.
- Cobertura: cobertura_files reads each class's line hits and condition-coverage
  with ElementTree, and the line-rate and branch-rate the producer wrote.
- Go coverprofile: coverprofile_rows expands each block holding a statement
  to every line l0 to l1, hit when any block over the line ran, and gives each
  span the caller names the lines it holds and no inner span holds.

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
import re
import tomllib
from typing import NamedTuple
import xml.etree.ElementTree as ET

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
    body: tuple  # loc.start, else decl.start

    @property
    def lines(self) -> tuple[int, float]:
        return self.start[0], self.end[0]

    @property
    def last_line(self) -> int:
        return int(self.start[0] if self.end[0] == END else self.end[0])


def _functions(data: dict) -> list[_Function]:
    return [_function(index, entry) for index, entry in data.get("fnMap", {}).items()]


def _function(index: str, entry: dict) -> _Function:
    """One fnMap entry: it opens at decl.start, its body at loc.start (else
    decl.start), and it ends at loc.end (else the end of its first line)."""
    start = _position(entry["decl"]["start"], 0)
    loc = entry.get("loc") or {}
    end = _position(loc.get("end"), END)
    end = end if end[0] != END else (start[0], END)
    body = _position(loc.get("start"), 0)
    return _Function(index, entry.get("name") or "(anonymous)", start, end,
                     body if body[0] else start)


def _innermost(holders: list[_Function]) -> _Function | None:
    """The one over the fewest lines; on a tie the one that starts later, then
    the one listed first."""
    return min(holders, key=lambda fn: (fn.lines[1] - fn.lines[0], _negated(fn.start)),
               default=None)


def _by_span(functions: list[_Function], start: tuple, end: tuple) -> _Function | None:
    """A branch's owner: the innermost function whose span holds its start."""
    return _innermost([fn for fn in functions if fn.start <= start <= fn.end])


def _by_body(functions: list[_Function], start: tuple, end: tuple) -> _Function | None:
    """A statement's owner: the innermost function whose body holds its start."""
    return _innermost([fn for fn in functions if fn.body <= start <= fn.end])


def _holds(fn: _Function, start: tuple, end: tuple) -> bool:
    inside = fn.start <= start and end <= fn.end
    return inside and (start, end) != (fn.start, fn.end)


def _by_position(functions: list[_Function], start: tuple, end: tuple) -> _Function | None:
    holders = [fn for fn in functions if _holds(fn, start, end)]
    return max(holders, key=lambda fn: (fn.start, _negated(fn.end)), default=None)


def _negated(point: tuple) -> tuple:
    return tuple(-value for value in point)


# rule -> (a branch's owner, a statement's owner)
OWNERS = {"line": (_by_span, _by_body), "position": (_by_position, _by_position)}


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
    branch_owner, statement_owner = OWNERS[rule]
    arms = _assign(functions, _branch_arms(data), branch_owner)
    stmts = _assign(functions, _statements(data), statement_owner)
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


def mixed_lines(data: dict) -> set[int]:
    """Lines of one istanbul file entry where a statement that starts there ran
    and another did not."""
    hits: dict[int, set[bool]] = {}
    for index, statement in data.get("statementMap", {}).items():
        hits.setdefault(statement["start"]["line"], set()).add(data["s"].get(index, 0) > 0)
    return {line for line, ran in hits.items() if ran == {True, False}}


# --- lcov ----------------------------------------------------------------------------------

def _source_file(rest: str, found: dict, current: list) -> None:
    current[:] = [found.setdefault(rest, {})]


def _line_count(rest: str, found: dict, current: list) -> None:
    number, count = rest.split(",")[:2]
    current[0][int(number)] = int(count)


LCOV_LINE_TAGS = {"SF": _source_file, "DA": _line_count}


def lcov_lines(text: str) -> dict[str, dict[int, int]]:
    """{SF path: {DA line: count}} from an lcov tracefile."""
    found: dict[str, dict[int, int]] = {}
    current: list = []
    for line in text.splitlines():
        tag, _, rest = line.partition(":")
        LCOV_LINE_TAGS.get(tag, lambda *_: None)(rest, found, current)
    return found


class LcovFile(NamedTuple):
    """One SF path's counts as the producer wrote them: its summary lines (LF,
    LH, BRF, BRH) and, per line, the BRDA arms and the arms taken."""
    summary: dict[str, int]
    arms: dict[int, tuple[int, int]]


def _arm(rest: str, file: LcovFile) -> None:
    """BRDA:<line>,<block>,<branch>,<taken>; `-` is a block that never ran."""
    line, taken = int(rest.split(",")[0]), rest.rsplit(",", 1)[1]
    total, hit = file.arms.get(line, (0, 0))
    file.arms[line] = (total + 1, hit + (taken != "-" and int(taken) > 0))


def lcov_files(text: str) -> dict[str, LcovFile]:
    """{SF path: LcovFile} from an lcov tracefile, the last record of a path
    the one kept (the recordings write each path once)."""
    found: dict[str, LcovFile] = {}
    file = None
    for line in text.splitlines():
        tag, _, rest = line.partition(":")
        if tag == "SF":
            file = found[rest] = LcovFile({}, {})
        elif tag == "BRDA":
            _arm(rest, file)
        elif tag in ("LF", "LH", "BRF", "BRH"):
            file.summary[tag] = int(rest)
    return found


def lcov_arms(text: str) -> dict[str, list[tuple[str, bool]]]:
    """{SF path: [(arm, taken)]} in file order. An arm is named `<line>.<k>`,
    k its order among its line's BRDA records; `-` and 0 are not taken."""
    found: dict[str, list[tuple[str, bool]]] = {}
    arms: list = []
    for line in text.splitlines():
        tag, _, rest = line.partition(":")
        if tag == "SF":
            arms = found.setdefault(rest, [])
        elif tag == "BRDA":
            number, taken = rest.split(",")[0], rest.rsplit(",", 1)[1]
            k = sum(name.startswith(number + ".") for name, _ in arms)
            arms.append((f"{number}.{k}", taken != "-" and int(taken) > 0))
    return found


def _owner(spans: list[tuple[str, int, int]], path: str, line: int) -> tuple | None:
    """The innermost span of the path holding the line: the fewest lines."""
    holders = [span for span in spans if span[0] == path and span[1] <= line <= span[2]]
    return min(holders, key=lambda span: span[2] - span[1], default=None)


def lcov_rows(text: str, spans: list[tuple[str, int, int]]) -> list[Counts]:
    """One row per (path, start, end) span: the DA lines and BRDA arms on the
    lines it holds and no inner span holds."""
    lines, arms = lcov_lines(text), lcov_arms(text)
    rows = []
    for span in spans:
        path = span[0]
        own = [(n, c) for n, c in sorted(lines.get(path, {}).items()) if _owner(spans, path, n) == span]
        mine = [(name, taken) for name, taken in arms.get(path, [])
                if _owner(spans, path, int(name.split(".")[0])) == span]
        rows.append(Counts(path, "", span[1], span[2], tuple(name for name, _ in mine),
                           tuple(name for name, taken in mine if taken), tuple(n for n, _ in own),
                           tuple(n for n, c in own if c > 0), any(c > 0 for _, c in own)))
    return rows


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


def _lanes(corpus: Path) -> list[dict]:
    """The corpus's [[lane]] tables; none when it holds no crapkit.toml, such as the
    small corpus of a tree change control seeds with only the files a rule reads."""
    config = corpus / "crapkit.toml"
    if not config.is_file():
        return []
    return tomllib.loads(config.read_text(encoding="utf-8")).get("lane", [])


def table(corpus: Path) -> dict[tuple[str, int], list[Counts]]:
    """{(path, start): rows} over every lane of the corpus's crapkit.toml whose
    command copies a recorded artifact into place."""
    found: dict[tuple[str, int], list[Counts]] = {}
    for lane in _lanes(corpus):
        for row in _lane_rows(corpus, lane):
            found.setdefault((row.path, row.start), []).append(row)
    return found


class CoberturaClass(NamedTuple):
    """One <class> as the producer wrote it: each line's hits, each branch
    line's (total, covered) from its condition-coverage, and the class's own
    line-rate and branch-rate."""
    filename: str
    hits: dict[int, int]
    conditions: dict[int, tuple[int, int]]
    line_rate: float
    branch_rate: float


_CONDITION = re.compile(r"[0-9.]+% \(([0-9]+)/([0-9]+)\)")


def _conditions(line: ET.Element) -> tuple[int, int] | None:
    found = _CONDITION.fullmatch(line.get("condition-coverage") or "")
    if line.get("branch") != "true" or found is None:
        return None
    return int(found[2]), int(found[1])


def cobertura_classes(data: bytes) -> list[CoberturaClass]:
    """Every <class> of a Cobertura report, in document order. The lines under
    <methods> are not read: the class's own <lines> list every line."""
    found = []
    for cls in ET.fromstring(data).iter("class"):
        lines = cls.findall("lines/line")
        conditions = {int(line.get("number")): _conditions(line) for line in lines}
        found.append(CoberturaClass(
            cls.get("filename"), {int(line.get("number")): int(line.get("hits")) for line in lines},
            {number: pair for number, pair in conditions.items() if pair},
            float(cls.get("line-rate")), float(cls.get("branch-rate"))))
    return found


class JacocoSourcefile(NamedTuple):
    """One <sourcefile> as JaCoCo wrote it, keyed by its package path: each
    <line>'s (mi, ci, mb, cb) and the sourcefile's own LINE and BRANCH
    <counter>s as (missed, covered)."""
    key: str
    lines: dict[int, tuple[int, int, int, int]]
    counters: dict[str, tuple[int, int]]


def jacoco_sourcefiles(data: bytes) -> list[JacocoSourcefile]:
    """Every <sourcefile> of a JaCoCo report, in document order, through any
    <group>. The DOCTYPE's report.dtd is never opened: ElementTree reads no
    external DTD."""
    found = []
    for package in ET.fromstring(data).iter("package"):
        prefix = package.get("name")
        for source in package.findall("sourcefile"):
            name = source.get("name")
            lines = {int(line.get("nr")): tuple(int(line.get(field)) for field in
                                                ("mi", "ci", "mb", "cb"))
                     for line in source.findall("line")}
            counters = {counter.get("type"): (int(counter.get("missed")),
                                              int(counter.get("covered")))
                        for counter in source.findall("counter")}
            found.append(JacocoSourcefile(f"{prefix}/{name}" if prefix else name, lines, counters))
    return found


def jacoco_rows(data: bytes, root: str, spans: list[tuple[str, int, int]]) -> list[Counts]:
    """One row per (path, start, end) span: the counted lines (mi + ci > 0) of
    the sourcefile keyed under `root` that the span holds and no inner span
    holds, and their branches as arms `<line>.<k>`. JaCoCo counts a line's
    branches and never names them, so the line's first cb arms are the taken ones."""
    files = {f"{root}/{source.key}": source.lines for source in jacoco_sourcefiles(data)}
    rows = []
    for span in spans:
        path = span[0]
        own = [(nr, counters) for nr, counters in sorted(files.get(path, {}).items())
               if _owner(spans, path, nr) == span]
        counted = [(nr, ci) for nr, (mi, ci, _, _) in own if mi + ci]
        arms = [(f"{nr}.{k}", k < cb) for nr, (_, _, mb, cb) in own for k in range(mb + cb)]
        rows.append(Counts(path, "", span[1], span[2], tuple(name for name, _ in arms),
                           tuple(name for name, taken in arms if taken),
                           tuple(nr for nr, _ in counted), tuple(nr for nr, ci in counted if ci),
                           any(ci for _, ci in counted)))
    return rows


_BLOCK = re.compile(r"^(.+):(\d+)\.\d+,(\d+)\.\d+ (\d+) (\d+)$")


def coverprofile_lines(text: str) -> dict[str, dict[int, bool]]:
    """{import path key: {line: hit}} from a Go coverprofile: every line l0 to
    l1 of a block with numStmt above 0, hit when any block over it counted
    above 0. A `mode:` line and a block with numStmt 0 give nothing."""
    files: dict[str, dict[int, bool]] = {}
    for line in text.splitlines():
        found = _BLOCK.match(line)
        if not found or int(found.group(4)) == 0:
            continue
        key, first, last, count = found.group(1), int(found.group(2)), int(found.group(3)), int(
            found.group(5))
        lines = files.setdefault(key, {})
        for number in range(first, last + 1):
            lines[number] = lines.get(number, False) or count > 0
    return files


def coverprofile_rows(text: str, module: str, root: str,
                      spans: list[tuple[str, int, int]]) -> list[Counts]:
    """One row per (path, start, end) span: the profile's lines of the file
    keyed `module/<file>`, read as `root/<file>`, that the span holds and no
    inner span holds. A coverprofile carries no branches."""
    files = {f"{root}/{key[len(module) + 1:]}": lines
             for key, lines in coverprofile_lines(text).items() if key.startswith(module + "/")}
    rows = []
    for span in spans:
        path = span[0]
        own = [(number, hit) for number, hit in sorted(files.get(path, {}).items())
               if _owner(spans, path, number) == span]
        rows.append(Counts(path, "", span[1], span[2], (), (), tuple(number for number, _ in own),
                           tuple(number for number, hit in own if hit), any(hit for _, hit in own)))
    return rows
