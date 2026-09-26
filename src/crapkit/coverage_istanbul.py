"""Attribute one decoded Istanbul file's branches and statements to functions.

Branch hits map into function spans by position, line and column: a branch
counts from the function's declaration on, a statement from its body on. A
function with no branches inside its span falls back to STATEMENT coverage in
that span, and only with no statements either to invocation (hit or not) — a
straight-line function half-executed must not read as fully covered. Written for the
AST-remapped output of @vitest/coverage-v8 >= 3.2, which is istanbul-schema-identical.

This module is also the istanbul adapter (coverage_format looks it up from a
lane's `parser`): it reads the artifact through covstream's framing, keys each
file by stripping the checkout root, and owns the advice a wrong-tree refusal
gives an istanbul lane. Attribution itself stays independent of file I/O and
JSON framing.
"""
from __future__ import annotations

import heapq
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from . import covstream
from .errors import ToolError

if TYPE_CHECKING:
    from .config import Lane


class FnCoverage(NamedTuple):
    name: str
    start: int
    end: int
    invoked: bool
    branches_total: int
    branches_covered: int
    statements_total: int = 0
    statements_covered: int = 0
    # The producer was told to leave the function out, so no test moves its number.
    excluded: bool = False
    # The reader lists every function the producer measured in this file, so a
    # function missing from the list was left out on purpose.
    full_listing: bool = False

    @property
    def coverage(self) -> float:
        if self.branches_total > 0:
            return self.branches_covered / self.branches_total
        if self.statements_total > 0:
            return self.statements_covered / self.statements_total
        return 1.0 if self.invoked else 0.0


def _rel_path(abs_path: str, repo_root: str) -> str:
    norm = abs_path.replace("\\", "/")
    root = repo_root.replace("\\", "/").rstrip("/") + "/"
    return norm[len(root):] if norm.startswith(root) else norm


# --- span attribution ------------------------------------------------------
# mutable span layout while attributing: [name, start, end, invoked, b_total, b_cov, s_total,
# s_cov, signature, body, finish], the last three as (line, column) positions
_B_TOTAL, _B_COV, _S_TOTAL, _S_COV = 4, 5, 6, 7
_SIGNATURE, _BODY, _FINISH = 8, 9, 10
_COUNTS = slice(0, 8)
_LINE_END = sys.maxsize


def coverage_count(value: object, field: str) -> int:
    """Admit a producer's count before attribution or ratio arithmetic."""
    if type(value) is float and value.is_integer():
        value = int(value)
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer count, got {value!r}")
    return value


class ClampedBranchCounts(list):
    """Function coverage for a file whose artifact carried negative branch counts.

    A branch count is DERIVED: @vitest/coverage-v8 takes an if/else pair's
    else-path as parent - if, and that subtraction underflows on remapped
    output. Refusing the file for it failed the lane, which left no baseline,
    which blocked every commit in the measured repo, in every language. The
    counter is clamped to 0 instead, so the branch reads uncovered and never
    negative, and `clamped` rides back with the rows so a run can count and
    name them the way UnanalyzableFile counts a reader refusal.
    """

    def __init__(self, rows, clamped: int) -> None:
        super().__init__(rows)
        self.clamped = clamped


def _admit_branch(value: object, field: str) -> int:
    """A derived branch counter, admitted with its underflow clamped away."""
    if type(value) is float and value.is_integer():
        value = int(value)
    if type(value) is int and value < 0:
        return 0
    return coverage_count(value, field)


def _admit_hits(cov: dict) -> int:
    """Admit one file's counters in place; answer how many branches were clamped.

    `f` and `s` stay strict. Those are measured hit counts, so a negative one is
    corruption and has never been seen: over the 4,166-file openclaw unit-fast
    artifact all 73 negatives sat in `b`, every one at index [1].
    """
    for group in ("f", "s"):
        for key, value in cov.get(group, {}).items():
            coverage_count(value, f"{group}[{key!r}]")
    clamped = 0
    for key, hits in cov.get("b", {}).items():
        for index, value in enumerate(hits):
            admitted = _admit_branch(value, f"b[{key!r}][{index}]")
            if admitted != value:
                hits[index] = admitted
                clamped += 1
    return clamped


def _position(where: dict, line: int, missing_column: int) -> tuple[int, int]:
    """(line, column) of one istanbul location. A column the producer leaves
    out, or writes as null, means the whole line: its first column at a
    start, its last at an end."""
    column = where.get("column")
    return line, column if type(column) is int else missing_column


def _body_start(fn: dict, signature: tuple[int, int]) -> tuple[int, int]:
    """Where the function's own code starts: `loc`, which istanbul puts on the
    body and v8-to-istanbul on the signature."""
    start = fn.get("loc", {}).get("start", {})
    line = start.get("line")
    return signature if line is None else _position(start, line, 0)


def _fn_span(name: str, fn: dict, invoked: bool) -> list:
    decl = fn["decl"]["start"]
    signature = _position(decl, decl["line"], 0)
    finish = fn.get("loc", {}).get("end", {})
    end = finish.get("line") or signature[0]
    return [name, signature[0], end, invoked, 0, 0, 0, 0,
            signature, _body_start(fn, signature), _position(finish, end, _LINE_END)]


def _fn_spans(cov: dict) -> list[list]:
    invoked = cov.get("f", {})
    spans = [_fn_span(fn.get("name") or "(anonymous)", fn, invoked.get(fid, 0) > 0)
             for fid, fn in cov.get("fnMap", {}).items()]
    spans.sort(key=lambda s: s[1])
    return spans


def _start_point(where: dict) -> tuple[int, int] | None:
    start = where.get("start", {})
    line = start.get("line")
    return None if line is None else _position(start, line, 0)


def _branch_point(branch: dict) -> tuple[int, int] | None:
    return _start_point(branch.get("loc", {}))


def _stmt_line(stmt: dict) -> int | None:
    return stmt.get("start", {}).get("line")


def _points(entries, point_of) -> set[tuple[int, int]]:
    """Every position the attribution will ask about for one kind of counter."""
    points = {point_of(entry) for entry in entries}
    points.discard(None)
    return points


def _push_started(heap: list, ordered: list[list], nxt: int, point: tuple, opens: int) -> int:
    while nxt < len(ordered) and ordered[nxt][opens] <= point:
        span = ordered[nxt]
        line, column = span[opens]
        heapq.heappush(heap, (span[2] - line, -line, -column, nxt, span))
        nxt += 1
    return nxt


def _drop_ended(heap: list, point: tuple) -> None:
    """Discard spans that closed before this point. Safe to do lazily and only at
    the top: query points only increase, so anything popped here can never
    contain a later point either."""
    while heap and heap[0][-1][_FINISH] < point:
        heapq.heappop(heap)


def _span_owners(fn_spans: list[list], points: set[tuple[int, int]],
                 opens: int) -> dict[tuple[int, int], list | None]:
    """position -> innermost containing span, each span opening at its `opens`
    position. A hit inside a nested function belongs to that function, never
    to its encloser — else the nested one reads through its encloser and the
    encloser answers for lines it can't fix. A counter that starts on a
    function's line but ahead of where that function opens is the encloser's.

    Sweeping spans by start into a heap keyed (lines, -start, index) settles
    that in O((F + Q) log F) instead of a scan per query. The index term is
    load-bearing: it is the sorted position, so an exact tie on (lines, -start)
    resolves to the span the old linear scan met first."""
    ordered = sorted(fn_spans, key=lambda s: s[opens])
    heap: list[tuple] = []
    owners: dict[tuple[int, int], list | None] = {}
    nxt = 0
    for point in sorted(points):
        nxt = _push_started(heap, ordered, nxt, point, opens)
        _drop_ended(heap, point)
        owners[point] = heap[0][-1] if heap else None
    return owners


def _attach_branches(fn_spans: list[list], cov: dict) -> None:
    """A function holds branches from its signature on: a default argument's
    arm sits between its name and its body. A ternary that opens ahead of a
    function on the same line (`flag ? (x) => x : y`) is the code around it."""
    branches = cov.get("branchMap", {})
    owners = _span_owners(fn_spans, _points(branches.values(), _branch_point), _SIGNATURE)
    hits_by_id = cov.get("b", {})
    for bid, branch in branches.items():
        best = owners.get(_branch_point(branch))
        if best is not None:
            hits = hits_by_id.get(bid, [])
            best[_B_TOTAL] += len(hits)
            best[_B_COV] += sum(1 for h in hits if h > 0)


def _attach_statements(fn_spans: list[list], cov: dict) -> None:
    """A function holds statements from its body on. `const f = (x) => ...`
    carries a statement for the declaration that starts at the arrow and runs
    when the declaration does, at import for a module-level arrow: the code
    around the arrow owns it, or an arrow no test calls reads half covered."""
    statements = cov.get("statementMap", {})
    owners = _span_owners(fn_spans, _points(statements.values(), _start_point), _BODY)
    hits_by_id = cov.get("s", {})
    for sid, stmt in statements.items():
        best = owners.get(_start_point(stmt))
        if best is not None:
            best[_S_TOTAL] += 1
            best[_S_COV] += 1 if hits_by_id.get(sid, 0) > 0 else 0


def _instrumented(cov: dict) -> bool:
    """Whether this file entry is an instrumenter's own output, whose fnMap
    lists every function it did not skip. istanbul writes a statement for
    each one it counts, so an entry with statements is; a hand-built entry
    that carries fnMap alone cannot say what it left out."""
    return bool(cov.get("statementMap"))


def _file_coverage(cov: dict) -> list[FnCoverage]:
    clamped = _admit_hits(cov)
    fn_spans = _fn_spans(cov)
    _attach_branches(fn_spans, cov)
    _attach_statements(fn_spans, cov)
    full = _instrumented(cov)
    rows = [FnCoverage(*s[_COUNTS], full_listing=full) for s in fn_spans]
    return ClampedBranchCounts(rows, clamped) if clamped else rows


def _dead_lines(cov: dict) -> set[int]:
    hits_by_id = cov.get("s", {})
    dead = {_stmt_line(stmt)
            for sid, stmt in cov.get("statementMap", {}).items()
            if hits_by_id.get(sid, 0) == 0}
    dead.discard(None)
    return dead


# --- reading the artifact ----------------------------------------------------

_BAD_ISTANBUL = "unparseable istanbul artifact"


def _istanbul_map(w, repo_root: str, per_file) -> dict:
    return {_rel_path(abs_path, repo_root): per_file(cov)
            for abs_path, cov in covstream.split_window(w)}


def _istanbul_both(w, repo_root: str) -> tuple[dict, dict]:
    per_file, dead = {}, {}
    for abs_path, cov in covstream.split_window(w):
        rel = _rel_path(abs_path, repo_root)
        per_file[rel] = _file_coverage(cov)
        dead[rel] = _dead_lines(cov)
    return per_file, dead


_CLAMPED_NAMED = 3


def _loudest_clamped(counts: dict[str, int]) -> list[tuple[str, int]]:
    """The files worth naming: most counters clamped first, ties by path."""
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:_CLAMPED_NAMED]


def _clamped_counts(per_file: dict) -> dict[str, int]:
    """path -> how many branch counters that file needed clamped, when any."""
    return {path: rows.clamped for path, rows in per_file.items()
            if getattr(rows, "clamped", 0)}


def _note_clamped_branches(per_file: dict) -> None:
    """Name the derived branch counters this artifact needed clamped.

    Loud but not fatal, the shape _note_unanalyzable settled for reader
    refusals: one underflowed counter degrades one branch measurement, and
    ending the run over it blocks every commit in the repo.
    """
    counts = _clamped_counts(per_file)
    if not counts:
        return
    print(f"crapkit: {sum(counts.values())} negative derived branch count(s) in "
          f"{len(counts)} file(s) clamped to 0; the producer's else-path subtraction "
          "underflowed and each such branch reads as uncovered:", file=sys.stderr)
    for path, count in _loudest_clamped(counts):
        print(f"crapkit:   {path}: {count}", file=sys.stderr)
    if len(counts) > _CLAMPED_NAMED:
        print(f"crapkit:   ... and {len(counts) - _CLAMPED_NAMED} more", file=sys.stderr)


def _require_files(per_file: dict) -> None:
    """A zero-file artifact scores as full coverage if it is let through."""
    if not per_file:
        raise ToolError(
            "istanbul artifact is empty (zero files) — the coverage run measured nothing")


def parse_istanbul_both_file(path: Path | str, *, repo_root: str, chunk: int = covstream.CHUNK
                             ) -> tuple[dict[str, list[FnCoverage]], dict[str, set[int]], str]:
    """Function coverage AND dead lines from ONE walk, plus the sha256 of the
    artifact's own bytes.

    verify asks both questions of every istanbul artifact: the lane wants
    function coverage, diff coverage wants the lines no statement ran. Asking
    them separately decoded every member twice: 12.85 s over 13 lanes of a
    31,459-file tree, against 7.80 s merged. Decoding is the whole cost;
    _dead_lines over an already decoded file is near free.
    """
    (per_file, dead), digest = covstream.read_walk(
        path, lambda w: _istanbul_both(w, repo_root), f"{_BAD_ISTANBUL} {path}", chunk)
    _require_files(per_file)
    _note_clamped_branches(per_file)
    return per_file, dead, digest


def parse_istanbul_missing_file(path: Path | str, *, repo_root: str,
                                chunk: int = covstream.CHUNK) -> dict[str, set[int]]:
    """Per measured file, the lines whose statement never ran."""
    missing, _ = covstream.read_walk(
        path, lambda w: _istanbul_map(w, repo_root, _dead_lines), _BAD_ISTANBUL, chunk)
    return missing


# --- the adapter a lane reads through ------------------------------------------
#
# The reader takes the checkout root, rebases every path under it and never
# reads path_prefix, so a path that stayed absolute came from another tree and
# no key on the lane can rebase it.

WRONG_TREE_FIX = ("The reader rebases every path under this checkout's root, so these were "
                  "written against another one: rerun the suite here rather than reusing an "
                  "artifact copied in or restored from a CI cache")
ABSOLUTE_FIX = ("The reader strips this checkout's root off every measured path "
                "literally, so the reporter spelled that root some other way: point "
                "it at this checkout with its own cwd/root option, then rerun the lane")
UNMEASURED_READING = "or the suite measured a part of the tree these scopes do not name"


def read(lane: Lane, root: Path, artifact: Path
         ) -> tuple[dict[str, list[FnCoverage]], dict[str, set[int]], str]:
    """The lane's function coverage, dead lines and artifact digest, one walk."""
    return parse_istanbul_both_file(artifact, repo_root=str(root))


def missing(lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]:
    """The lines no statement ran, per measured file."""
    return parse_istanbul_missing_file(artifact, repo_root=str(root))


def contexts(lane: Lane, root: Path, artifact: Path, source_path: str) -> dict[int, list[str]]:
    """Istanbul records no per-line test contexts, so there is nothing to read."""
    return {}


def as_reported(lane: Lane, key: str) -> str:
    """The key as the runner wrote it. The reader only ever strips the root, so
    a key that still looks absolute is spelled the way the artifact spells it,
    and path_prefix, which this reader never adds, is never taken off."""
    return key
