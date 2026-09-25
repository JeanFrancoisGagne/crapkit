"""Attribute one decoded Istanbul file's branches and statements to functions.

Branch hits map into function spans by line containment. A function with no
branches inside its span falls back to STATEMENT coverage in that span, and
only with no statements either to invocation (hit or not) — a straight-line
function half-executed must not read as fully covered. Written for the
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
from .repotext import json_kind

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
# mutable span layout while attributing: [name, start, end, invoked, b_total, b_cov, s_total, s_cov]
_B_TOTAL, _B_COV, _S_TOTAL, _S_COV = 4, 5, 6, 7


def coverage_count(value: object, field: str) -> int:
    """Admit a producer's count before attribution or ratio arithmetic."""
    if type(value) is float and value.is_integer():
        value = int(value)
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer count, got {value!r}; "
                         f"{covstream.REGENERATE}")
    return value


def _field(node: object, key: str) -> object:
    """`node[key]` when node is an object, else None: a reader that walks a
    path of fields gets None at the first step that is not there."""
    return node.get(key) if isinstance(node, dict) else None


def _at(node: object, *keys: str) -> object:
    for key in keys:
        node = _field(node, key)
    return node


def _object(where: str, value: object) -> dict:
    """A field istanbul always writes as an object, or the refusal naming it.
    `where` is the name as the refusal prints it: a field in backticks."""
    if not isinstance(value, dict):
        raise ValueError(f"{where} holds {json_kind(value)}, not an object; "
                         f"{covstream.REGENERATE}")
    return value


def _branch_counts(key: str, hits: object) -> list:
    if not isinstance(hits, list):
        raise ValueError(f"`b[{key!r}]` holds {json_kind(hits)}, not an array of branch "
                         f"counts; {covstream.REGENERATE}")
    return hits


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
    return sum(_admit_branch_hits(key, hits) for key, hits in cov.get("b", {}).items())


def _admit_branch_hits(key: str, hits: object) -> int:
    """Admit one branch's counters in place; answer how many were clamped."""
    clamped = 0
    for index, value in enumerate(_branch_counts(key, hits)):
        admitted = _admit_branch(value, f"b[{key!r}][{index}]")
        if admitted != value:
            hits[index] = admitted
            clamped += 1
    return clamped


def _fn_end(fid: str, fn: dict) -> int:
    """The line a function's span ends on: loc.end.line, which every istanbul
    producer writes. Read as the declaration line when it was missing, the span
    shrank to one line, the body's branches attached to nothing, and an invoked
    function scored as covered."""
    line = _at(fn, "loc", "end", "line")
    if type(line) is not int:
        raise ValueError(f"fnMap[{fid!r}] has no loc.end.line (every istanbul reporter "
                         "writes one; regenerate the artifact with the runner's reporter)")
    return line


def _decl_line(fid: str, fn: object) -> int:
    """The line a function is declared on, decl.start.line. istanbul 0.x wrote
    no decl, and a function without one has no start to own its lines from."""
    line = _at(fn, "decl", "start", "line")
    if type(line) is not int:
        raise ValueError(f"fnMap[{fid!r}] has no decl.start.line (every istanbul reporter "
                         "writes one; regenerate the artifact with the runner's reporter)")
    return line


# Each map istanbul writes, the counter group that pairs with it, and what one
# entry of it is.
_COUNTED = (("fnMap", "f", "function"), ("statementMap", "s", "statement"),
            ("branchMap", "b", "branch"))


def _require_counters(cov: object) -> dict:
    """`cov` once every mapped id has its hit counter.

    istanbul's writers pair every fnMap, statementMap and branchMap entry with a
    counter in f, s and b. A salvage merged by hand or a converter can drop one,
    and attribution read the absent counter as a zero: a statement that never
    ran, a branch pair that did not exist, a function never called. The score
    moved with nothing said, so the artifact is refused instead, like a negative
    counter in f or s. A map or a counter group that is there but holds no
    object is refused naming it.
    """
    cov = _object("the file entry", cov)
    for mapped, counters, kind in _COUNTED:
        _require_counter_group(cov, mapped, counters, kind)
    return cov


def _require_counter_group(cov: dict, mapped: str, counters: str, kind: str) -> None:
    hits = _object(f"`{counters}`", cov.get(counters, {}))
    missing = [key for key in _object(f"`{mapped}`", cov.get(mapped, {})) if key not in hits]
    if missing:
        raise ValueError(
            f"{kind} {missing[0]!r} has no hit count in `{counters}`, so crapkit cannot "
            f"tell whether it ran ({len(missing)} such in this file); regenerate the artifact "
            "with the coverage tool, or merge shards with one that keeps every counter")


def _fn_spans(cov: dict) -> list[list]:
    spans = []
    for fid, fn in cov.get("fnMap", {}).items():
        start = _decl_line(fid, fn)
        end = _fn_end(fid, fn)
        invoked = cov.get("f", {}).get(fid, 0) > 0
        spans.append([_field(fn, "name") or "(anonymous)", start, end, invoked, 0, 0, 0, 0])
    spans.sort(key=lambda s: s[1])
    return spans


def _branch_line(bid: str, branch: dict) -> int:
    """Where a branch sits: loc.start.line, else the `line` producers write
    beside it. Without the fallback a branch with no loc attached to no
    function. A branch with neither was left out, and the function it sat in
    lost its arms with nothing said, so the artifact is refused instead."""
    line = _at(branch, "loc", "start", "line")
    line = _field(branch, "line") if type(line) is not int else line
    if type(line) is not int:
        raise ValueError(f"branchMap[{bid!r}] has no loc.start.line and no line (every istanbul "
                         "reporter writes one; regenerate the artifact with the runner's reporter)")
    return line


def _stmt_line(sid: str, stmt: object) -> int | None:
    """Where a statement starts. A start with no line leaves the statement out,
    as it always has; a statement or a start that is no object is refused."""
    start = _object(f"`statementMap[{sid!r}]`", stmt).get("start", {})
    line = _object(f"`statementMap[{sid!r}].start`", start).get("line")
    if line is not None and type(line) is not int:
        raise ValueError(f"`statementMap[{sid!r}].start.line` holds {json_kind(line)}, not a "
                         f"line number; {covstream.REGENERATE}")
    return line


def _query_lines(cov: dict) -> set[int]:
    """Every line the attribution will ask about, branches and statements both."""
    lines = {_branch_line(bid, b) for bid, b in cov.get("branchMap", {}).items()}
    lines |= {_stmt_line(sid, s) for sid, s in cov.get("statementMap", {}).items()}
    lines.discard(None)
    return lines


def _push_started(heap: list, ordered: list[list], nxt: int, line: int) -> int:
    while nxt < len(ordered) and ordered[nxt][1] <= line:
        span = ordered[nxt]
        heapq.heappush(heap, (span[2] - span[1], -span[1], nxt, span))
        nxt += 1
    return nxt


def _drop_ended(heap: list, line: int) -> None:
    """Discard spans that closed before this line. Safe to do lazily and only at
    the top: query lines only increase, so anything popped here can never
    contain a later line either."""
    while heap and heap[0][3][2] < line:
        heapq.heappop(heap)


def _span_owners(fn_spans: list[list], lines: set[int]) -> dict[int, list | None]:
    """line -> innermost containing span. A hit inside a nested function belongs
    to that function, never to its encloser — else the nested one reads through
    its encloser and the encloser answers for lines it can't fix.

    Sweeping spans by start into a heap keyed (length, -start, index) settles
    that in O((F + Q) log F) instead of a scan per query. The index term is
    load-bearing: it is the sorted position, so an exact tie on (length, -start)
    resolves to the span the old linear scan met first."""
    ordered = sorted(fn_spans, key=lambda s: s[1])
    heap: list[tuple] = []
    owners: dict[int, list | None] = {}
    nxt = 0
    for line in sorted(lines):
        nxt = _push_started(heap, ordered, nxt, line)
        _drop_ended(heap, line)
        owners[line] = heap[0][3] if heap else None
    return owners


def _attach_branches(owners: dict[int, list | None], cov: dict) -> None:
    hits_by_id = cov.get("b", {})
    for bid, branch in cov.get("branchMap", {}).items():
        best = owners.get(_branch_line(bid, branch))
        if best is not None:
            hits = hits_by_id.get(bid, [])
            best[_B_TOTAL] += len(hits)
            best[_B_COV] += sum(1 for h in hits if h > 0)


def _attach_statements(owners: dict[int, list | None], cov: dict) -> None:
    hits_by_id = cov.get("s", {})
    for sid, stmt in cov.get("statementMap", {}).items():
        best = owners.get(_stmt_line(sid, stmt))
        if best is not None:
            best[_S_TOTAL] += 1
            best[_S_COV] += 1 if hits_by_id.get(sid, 0) > 0 else 0


def _file_coverage(cov: dict) -> list[FnCoverage]:
    clamped = _admit_hits(cov)
    fn_spans = _fn_spans(cov)
    owners = _span_owners(fn_spans, _query_lines(cov))
    _attach_branches(owners, cov)
    _attach_statements(owners, cov)
    rows = [FnCoverage(*s) for s in fn_spans]
    return ClampedBranchCounts(rows, clamped) if clamped else rows


def _read_file(rel: str, cov: object) -> list[FnCoverage]:
    """One file's function coverage, once its counters are all there."""
    return _named_file(rel, lambda: _file_coverage(_require_counters(cov)))


def _named_file(rel: str, read):
    """What `read` makes of one file, or its refusal with the file named: an
    fnMap or branchMap id alone does not say which of the artifact's files
    holds it."""
    try:
        return read()
    except ValueError as exc:
        raise ValueError(f"{rel}: {exc}") from exc


def _dead_lines(cov: dict) -> set[int]:
    hits_by_id = cov.get("s", {})
    dead = {_stmt_line(sid, stmt)
            for sid, stmt in cov.get("statementMap", {}).items()
            if hits_by_id.get(sid, 0) == 0}
    dead.discard(None)
    return dead


# --- reading the artifact ----------------------------------------------------

_BAD_ISTANBUL = "unparseable istanbul artifact"


def _istanbul_map(w, repo_root: str, per_file) -> dict:
    out = {}
    for abs_path, cov in covstream.split_window(w):
        rel = _rel_path(abs_path, repo_root)
        out[rel] = _named_file(rel, lambda: per_file(_require_counters(cov)))
    return out


def _istanbul_both(w, repo_root: str) -> tuple[dict, dict]:
    per_file, dead = {}, {}
    for abs_path, cov in covstream.split_window(w):
        rel = _rel_path(abs_path, repo_root)
        per_file[rel] = _read_file(rel, cov)
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
            "istanbul artifact is empty (zero files) — the coverage run measured nothing; "
            "rerun the lane and check that its command runs the tests")


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
