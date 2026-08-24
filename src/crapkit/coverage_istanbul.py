"""Istanbul coverage-final.json parser. Pure: JSON text in, per-file function coverage out.

Branch hits map into function spans by line containment. A function with no
branches inside its span falls back to STATEMENT coverage in that span, and
only with no statements either to invocation (hit or not) — a straight-line
function half-executed must not read as fully covered. Written for the
AST-remapped output of @vitest/coverage-v8 >= 3.2, which is istanbul-schema-identical.

The artifact is a flat {abs_path: coverage} object and only one file's coverage
is ever needed at a time, so the outer object is SPLIT rather than parsed:
split_top_level walks the members and decodes each value on its own. A
whole-document json.loads on a 157 MB artifact peaked at 1,432 MB against
335 MB for the split, for the same output.
"""
from __future__ import annotations

import heapq
import json
import re
from typing import Iterator, NamedTuple

from .errors import ToolError


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


# --- top-level splitter ----------------------------------------------------
# One member's opening: whitespace, the separating comma, the key string, the
# colon. The key pattern is string-aware — an escaped quote inside a Windows
# path must not end it — and its alternation is unambiguous (a character is
# either not a quote/backslash, or an escape pair), so it scans linearly and
# cannot backtrack.
_MEMBER_RE = re.compile(r'\s*,?\s*("(?:[^"\\]|\\.)*")\s*:\s*', re.DOTALL)
_OPEN_RE = re.compile(r"\s*\{")
_CLOSE_RE = re.compile(r"\s*\}\s*\Z")

# raw_decode reads ONE value at a position and reports where it ended, which is
# what makes the split possible without writing a second parser. Hand-rolled
# brace matching finds the same boundary but pays a Python loop iteration per
# structural character: 4.75s of a 5.4s parse on a 21.6 MB artifact, against
# 0.23s here, because this scanner is the C one.
_DECODER = json.JSONDecoder()


def split_top_level(text: str) -> Iterator[tuple[str, object]]:
    """Yield (key, value) per member of the outer object, decoding one value at
    a time. Equivalent to json.loads(text).items() except that the whole
    document is never a live dict: each value is dropped as the caller steps
    past it, so peak memory tracks the LARGEST file, not their sum."""
    opening = _OPEN_RE.match(text)
    if opening is None:
        raise ValueError("istanbul artifact is not a JSON object")
    i = opening.end()
    while True:
        member = _MEMBER_RE.match(text, i)
        if member is None:
            _expect_end(text, i)
            return
        value, i = _DECODER.raw_decode(text, member.end())
        yield json.loads(member.group(1)), value


def _expect_end(text: str, i: int) -> None:
    if _CLOSE_RE.match(text, i) is None:
        raise ValueError(f"unexpected content at offset {i}")


def _iter_files(text: str, repo_root: str) -> Iterator[tuple[str, dict]]:
    """(repo-relative path, coverage object) per artifact member. One file's
    coverage is live at a time; the previous one is unreferenced on the next step."""
    for abs_path, cov in split_top_level(text):
        yield _rel_path(abs_path, repo_root), cov


# --- span attribution ------------------------------------------------------
# mutable span layout while attributing: [name, start, end, invoked, b_total, b_cov, s_total, s_cov]
_B_TOTAL, _B_COV, _S_TOTAL, _S_COV = 4, 5, 6, 7


def _fn_spans(cov: dict) -> list[list]:
    spans = []
    for fid, fn in cov.get("fnMap", {}).items():
        start = fn["decl"]["start"]["line"]
        end = fn.get("loc", {}).get("end", {}).get("line") or start
        invoked = cov.get("f", {}).get(fid, 0) > 0
        spans.append([fn.get("name") or "(anonymous)", start, end, invoked, 0, 0, 0, 0])
    spans.sort(key=lambda s: s[1])
    return spans


def _branch_line(branch: dict) -> int | None:
    return branch.get("loc", {}).get("start", {}).get("line")


def _stmt_line(stmt: dict) -> int | None:
    return stmt.get("start", {}).get("line")


def _query_lines(cov: dict) -> set[int]:
    """Every line the attribution will ask about, branches and statements both."""
    lines = {_branch_line(b) for b in cov.get("branchMap", {}).values()}
    lines |= {_stmt_line(s) for s in cov.get("statementMap", {}).values()}
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
        best = owners.get(_branch_line(branch))
        if best is not None:
            hits = hits_by_id.get(bid, [])
            best[_B_TOTAL] += len(hits)
            best[_B_COV] += sum(1 for h in hits if h > 0)


def _attach_statements(owners: dict[int, list | None], cov: dict) -> None:
    hits_by_id = cov.get("s", {})
    for sid, stmt in cov.get("statementMap", {}).items():
        best = owners.get(_stmt_line(stmt))
        if best is not None:
            best[_S_TOTAL] += 1
            best[_S_COV] += 1 if hits_by_id.get(sid, 0) > 0 else 0


def _file_coverage(cov: dict) -> list[FnCoverage]:
    fn_spans = _fn_spans(cov)
    owners = _span_owners(fn_spans, _query_lines(cov))
    _attach_branches(owners, cov)
    _attach_statements(owners, cov)
    return [FnCoverage(*s) for s in fn_spans]


def _dead_lines(cov: dict) -> set[int]:
    hits_by_id = cov.get("s", {})
    dead = {_stmt_line(stmt)
            for sid, stmt in cov.get("statementMap", {}).items()
            if hits_by_id.get(sid, 0) == 0}
    dead.discard(None)
    return dead


def parse_istanbul_missing(text: str, *, repo_root: str) -> dict[str, set[int]]:
    """Per measured file, the lines whose statement never ran — the
    diff-coverage ground truth. Files with everything executed map to set()."""
    try:
        missing: dict[str, set[int]] = {}
        for rel_path, cov in _iter_files(text, repo_root):
            missing[rel_path] = _dead_lines(cov)
        return missing
    except Exception as exc:
        raise ToolError(f"unparseable istanbul artifact: {exc}") from exc


def parse_istanbul(text: str, *, repo_root: str) -> dict[str, list[FnCoverage]]:
    try:
        per_file: dict[str, list[FnCoverage]] = {}
        for rel_path, cov in _iter_files(text, repo_root):
            per_file[rel_path] = _file_coverage(cov)
        if not per_file:
            raise ToolError("istanbul artifact is empty (zero files) — the coverage run measured nothing")
        return per_file
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"unparseable istanbul artifact: {exc}") from exc
