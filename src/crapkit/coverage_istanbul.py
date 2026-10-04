"""Attribute one decoded Istanbul file's branches and statements to functions.

Branch hits map into function spans by position, line and column: a branch
counts from the function's declaration on, a statement from its body on. A
function with no branches inside its span falls back to STATEMENT coverage in
that span, and only with no statements either to invocation (hit or not) — a
straight-line function half-executed must not read as fully covered. Written for the
AST-remapped output of @vitest/coverage-v8 >= 3.2, which is istanbul-schema-identical.

This module is also the istanbul adapter (coverage_format looks it up from a
lane's `parser`): it reads the artifact through covstream's framing, keys each
file by rebasing it under the checkout root, records each key it could not
rebase with the placing step's reason, and owns the advice a wrong-tree refusal
gives an istanbul lane. Attribution itself stays independent of file I/O and
JSON framing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import covstream, score
from .errors import ToolError
from .istanbul_lines import on_reader_lines
from .repopath import Reported
from .repotext import json_kind

if TYPE_CHECKING:
    from .config import Lane
    from .repopath import Unplaced


# --- span attribution ------------------------------------------------------
# mutable span layout while attributing: [name, start, end, invoked, b_total, b_cov, s_total,
# s_cov, signature, body, finish], the last three as (line, column) positions; the
# finish sits where score.span_owners reads it, at score.SPAN_FINISH
_B_TOTAL, _B_COV, _S_TOTAL, _S_COV = 4, 5, 6, 7
_SIGNATURE, _BODY = 8, 9
_COUNTS = slice(0, 8)
_LINE_END = sys.maxsize


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
    return score.coverage_count(value, field)


def _admit_hits(cov: dict) -> int:
    """Admit one file's counters in place; answer how many branches were clamped.

    `f` and `s` stay strict. Those are measured hit counts, so a negative one is
    corruption and has never been seen: over the 4,166-file openclaw unit-fast
    artifact all 73 negatives sat in `b`, every one at index [1].
    """
    for group in ("f", "s"):
        for key, value in cov.get(group, {}).items():
            score.coverage_count(value, f"{group}[{key!r}]")
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
    _require_branch_paths(cov)
    return cov


_REGENERATE = ("regenerate the artifact with the coverage tool, or merge shards with one "
               "that keeps every counter")


def _require_counter_group(cov: dict, mapped: str, counters: str, kind: str) -> None:
    hits = _object(f"`{counters}`", cov.get(counters, {}))
    missing = [key for key in _object(f"`{mapped}`", cov.get(mapped, {})) if key not in hits]
    if missing:
        raise ValueError(
            f"{kind} {missing[0]!r} has no hit count in `{counters}`, so crapkit cannot "
            f"tell whether it ran ({len(missing)} such in this file); {_REGENERATE}")


def _location_count(branch: object) -> int | None:
    """How many paths a branchMap entry lists, or None for an entry with no
    `locations` list, such as a hand-built one that carries only `loc`."""
    locations = branch.get("locations") if isinstance(branch, dict) else None
    return len(locations) if isinstance(locations, list) else None


def _miscounted_branches(cov: dict) -> list[tuple[str, int, int]]:
    """(id, hit counts, locations) for each branch whose `b` array is not one
    hit count per location."""
    hits = cov.get("b", {})
    wrong = []
    for key, branch in cov.get("branchMap", {}).items():
        paths = _location_count(branch)
        if paths is not None and isinstance(hits[key], list) and len(hits[key]) != paths:
            wrong.append((key, len(hits[key]), paths))
    return wrong


def _require_branch_paths(cov: dict) -> None:
    """istanbul writes one hit count in `b` per location of a branchMap entry.
    Attribution counts the hit counts, so an array cut short read the paths it
    lost as no path at all: an if/else at [1] scored 1 of 1, and at [] it fell
    back to its statements. An array longer than the locations counts paths
    the branch does not have."""
    wrong = _miscounted_branches(cov)
    if wrong:
        key, counted, paths = wrong[0]
        raise ValueError(
            f"branch {key!r} has {counted} hit count(s) in `b` for its {paths} "
            f"location(s), so crapkit cannot tell which of its paths ran ({len(wrong)} such "
            f"in this file); {_REGENERATE}")


def _position(where: dict, line: int, missing_column: int) -> tuple[int, int]:
    """(line, column) of one istanbul location. A column the producer leaves
    out, or writes as null, means the whole line: its first column at a
    start, its last at an end."""
    column = where.get("column")
    return line, column if type(column) is int else missing_column


def _body_start(fn: dict, signature: tuple[int, int]) -> tuple[int, int]:
    """Where the function's own code starts: `loc`, which istanbul puts on the
    body and v8-to-istanbul on the signature."""
    start = _at(fn, "loc", "start")
    line = _field(start, "line")
    return signature if type(line) is not int else _position(start, line, 0)


def _fn_span(fid: str, fn: dict, invoked: bool) -> list:
    """One function's mutable span, its declaration and end lines read (or
    refused) the way every istanbul reporter writes them. The lines are read
    first: a function with no decl is refused in words, not as a KeyError."""
    line = _decl_line(fid, fn)
    end = _fn_end(fid, fn)
    signature = _position(fn["decl"]["start"], line, 0)
    return [_field(fn, "name") or "(anonymous)", signature[0], end, invoked, 0, 0, 0, 0,
            signature, _body_start(fn, signature), _position(fn["loc"]["end"], end, _LINE_END)]


def _fn_spans(cov: dict) -> list[list]:
    invoked = cov.get("f", {})
    spans = [_fn_span(fid, fn, invoked.get(fid, 0) > 0) for fid, fn in cov.get("fnMap", {}).items()]
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


def _branch_point(bid: str, branch: dict) -> tuple[int, int]:
    """(line, column) where a branch sits, its line read as _branch_line reads it."""
    line = _branch_line(bid, branch)
    start = _at(branch, "loc", "start")
    return _position(start if isinstance(start, dict) else {}, line, 0)


def _stmt_line(sid: str, stmt: object) -> int | None:
    """Where a statement starts. A start with no line leaves the statement out,
    as it always has; a statement or a start that is no object is refused."""
    start = _object(f"`statementMap[{sid!r}]`", stmt).get("start", {})
    line = _object(f"`statementMap[{sid!r}].start`", start).get("line")
    if line is not None and type(line) is not int:
        raise ValueError(f"`statementMap[{sid!r}].start.line` holds {json_kind(line)}, not a "
                         f"line number; {covstream.REGENERATE}")
    return line


def _stmt_point(sid: str, stmt: object) -> tuple[int, int] | None:
    """(line, column) where a statement starts, or None for a start with no line."""
    line = _stmt_line(sid, stmt)
    return None if line is None else _position(stmt["start"], line, 0)


def _points(entries: dict, point_of) -> set[tuple[int, int]]:
    """Every position the attribution will ask about for one kind of counter."""
    points = {point_of(key, entry) for key, entry in entries.items()}
    points.discard(None)
    return points


def _attach_branches(fn_spans: list[list], cov: dict) -> None:
    """A function holds branches from its signature on: a default argument's
    arm sits between its name and its body. A ternary that opens ahead of a
    function on the same line (`flag ? (x) => x : y`) is the code around it."""
    branches = cov.get("branchMap", {})
    owners = score.span_owners(fn_spans, _points(branches, _branch_point), _SIGNATURE)
    hits_by_id = cov.get("b", {})
    for bid, branch in branches.items():
        best = owners.get(_branch_point(bid, branch))
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
    owners = score.span_owners(fn_spans, _points(statements, _stmt_point), _BODY)
    hits_by_id = cov.get("s", {})
    for sid, stmt in statements.items():
        best = owners.get(_stmt_point(sid, stmt))
        if best is not None:
            best[_S_TOTAL] += 1
            best[_S_COV] += 1 if hits_by_id.get(sid, 0) > 0 else 0


def _instrumented(cov: dict) -> bool:
    """Whether this file entry is an instrumenter's own output, whose fnMap
    lists every function it did not skip. istanbul writes a statement for
    each one it counts, so an entry with statements is; a hand-built entry
    that carries fnMap alone cannot say what it left out."""
    return bool(cov.get("statementMap"))


def _file_coverage(cov: dict) -> list[score.FnCoverage]:
    clamped = _admit_hits(cov)
    fn_spans = _fn_spans(cov)
    _attach_branches(fn_spans, cov)
    _attach_statements(fn_spans, cov)
    full = _instrumented(cov)
    rows = [score.FnCoverage(*s[_COUNTS], full_listing=full) for s in fn_spans]
    return ClampedBranchCounts(rows, clamped) if clamped else rows


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


def _records(w, repo_root: str, keys: Reported | None = None):
    """(repo-relative path, record) per measured file, once every counter of the
    record is there, with its positions on the lines the reader numbers that file
    by (istanbul_lines). A refusal names the file. `keys` records each key it
    left unplaced."""
    keys = Reported(repo_root) if keys is None else keys
    for abs_path, cov in covstream.split_window(w):
        rel = keys(abs_path)
        yield rel, _named_file(rel, lambda: on_reader_lines(_require_counters(cov),
                                                            Path(repo_root, rel)))


def _istanbul_map(w, repo_root: str, per_file) -> dict:
    return {rel: _named_file(rel, lambda: per_file(cov)) for rel, cov in _records(w, repo_root)}


def _istanbul_both(w, repo_root: str, keys: Reported | None = None) -> tuple[dict, dict]:
    per_file, dead = {}, {}
    for rel, cov in _records(w, repo_root, keys):
        per_file[rel] = _named_file(rel, lambda: _file_coverage(cov))
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
            "istanbul artifact is empty (zero files) - the coverage run measured nothing; "
            "rerun the lane and check that its command runs the tests")


def parse_istanbul_both_file(path: Path | str, *, repo_root: str, chunk: int = covstream.CHUNK,
                             reported: Reported | None = None
                             ) -> tuple[dict[str, list[score.FnCoverage]], dict[str, set[int]], str]:
    """Function coverage AND dead lines from ONE walk, plus the sha256 of the
    artifact's own bytes. `reported` keys the files, and holds the keys it left
    unplaced when the walk is done; one of the reader's own by default.

    verify asks both questions of every istanbul artifact: the lane wants
    function coverage, diff coverage wants the lines no statement ran. Asking
    them separately decoded every member twice: 12.85 s over 13 lanes of a
    31,459-file tree, against 7.80 s merged. Decoding is the whole cost;
    _dead_lines over an already decoded file is near free.
    """
    (per_file, dead), digest = covstream.read_walk(
        path, lambda w: _istanbul_both(w, repo_root, reported), f"{_BAD_ISTANBUL} {path}", chunk)
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
# The reader takes the checkout root, rebases every path that resolves under it
# whatever its spelling, and never reads path_prefix, so a path that stayed
# absolute came from another tree and no key on the lane can rebase it.

WRONG_TREE_FIX = ("The reader rebases every path under this checkout's root, so these were "
                  "written against another one: rerun the suite here rather than reusing an "
                  "artifact copied in or restored from a CI cache")
# Reached only by a path this platform cannot open: repopath's reported entry
# rebases every other spelling of this checkout before the wrong-tree check
# reads a key.
ABSOLUTE_FIX = ("The reader rebases every measured path that resolves under this "
                "checkout, and these could not be opened here: rerun the lane on this "
                "machine rather than reusing a report written somewhere else")
UNMEASURED_READING = "or the suite measured a part of the tree these scopes do not name"
TAKES_PATH_PREFIX = False


def read(lane: Lane, root: Path, artifact: Path, *,
         unplaced: dict[str, Unplaced] | None = None
         ) -> tuple[dict[str, list[score.FnCoverage]], dict[str, set[int]], str]:
    """The lane's function coverage, dead lines and artifact digest, one walk.
    `unplaced` takes each key the walk could not rebase, with its reason: the
    keys it rebased are in the checkout, so none of them is there."""
    keys = Reported(str(root))
    both = parse_istanbul_both_file(artifact, repo_root=str(root), reported=keys)
    if unplaced is not None:
        unplaced.update(keys.unplaced)
    return both


def missing(lane: Lane, root: Path, artifact: Path) -> dict[str, set[int]]:
    """The lines no statement ran, per measured file."""
    return parse_istanbul_missing_file(artifact, repo_root=str(root))


def contexts(lane: Lane, root: Path, artifact: Path, source_path: str) -> dict[int, list[str]]:
    """Istanbul records no per-line test contexts, so there is nothing to read."""
    return {}
