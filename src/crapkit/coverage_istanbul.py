"""Attribute one decoded Istanbul file's branches and statements to functions.

Branch hits map into function spans by line containment. A function with no
branches inside its span falls back to STATEMENT coverage in that span, and
only with no statements either to invocation (hit or not) — a straight-line
function half-executed must not read as fully covered. Written for the
AST-remapped output of @vitest/coverage-v8 >= 3.2, which is istanbul-schema-identical.

covstream owns artifact decoding. This module receives one decoded file and
keeps attribution independent of file I/O and JSON framing.
"""
from __future__ import annotations

import heapq
from typing import NamedTuple

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
        raise ValueError(f"{field} must be a nonnegative integer count, got {value!r}")
    return value


def _admit_hits(cov: dict) -> None:
    for group in ("f", "s"):
        for key, value in cov.get(group, {}).items():
            coverage_count(value, f"{group}[{key!r}]")
    for key, hits in cov.get("b", {}).items():
        for index, value in enumerate(hits):
            coverage_count(value, f"b[{key!r}][{index}]")


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
    _admit_hits(cov)
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
