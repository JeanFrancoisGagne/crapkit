"""Near-duplicate function detection. Pure: inventory rows + file texts in,
ranked pairs out.

Normalized line shingles with CONTAINMENT scoring (shared / smaller set), so a
copy-paste that later grew a few lines still surfaces. An inverted shingle
index keeps a 14k-function repo tractable: only pairs that actually share a
shingle are ever compared. Tiny functions are structural noise and stay out.
"""
from __future__ import annotations

from .snapshot import InventoryRow

WINDOW = 4  # consecutive normalized lines per shingle
_COMMENT_PREFIXES = ("#", "//", "/*", "*", '"""', "'''")


def _normalized_lines(file_lines: list[str], start: int, end: int) -> list[str]:
    picked = []
    for raw in file_lines[start - 1:end]:
        line = "".join(raw.split())  # whitespace never distinguishes a clone
        if line and not raw.strip().startswith(_COMMENT_PREFIXES):
            picked.append(line)
    return picked


def _shingles(lines: list[str]) -> set[int]:
    return {hash(tuple(lines[i:i + WINDOW])) for i in range(len(lines) - WINDOW + 1)}


def _split_once(path: str, sources: dict[str, str]) -> list[str] | None:
    text = sources.get(path)
    return None if text is None else text.splitlines()


def _row_shingles(r: InventoryRow, file_lines: list[str], min_lines: int) -> set[int] | None:
    lines = _normalized_lines(file_lines, r.start, r.end)
    return _shingles(lines) if len(lines) >= min_lines else None


def _function_shingles(rows: list[InventoryRow], sources: dict[str, str],
                       min_lines: int) -> list[tuple[InventoryRow, set[int]]]:
    # Rows arrive ordered by (scope, path, start), so every row of a file is
    # contiguous: a ONE-ENTRY cache splits each source once instead of once per
    # function in it (measured 3 GB of re-split text on a 104 MB repo). A file
    # whose rows are NOT contiguous still scores identically, just re-split.
    out = []
    cached_path, file_lines = None, None
    for r in rows:
        if r.path != cached_path:
            cached_path, file_lines = r.path, _split_once(r.path, sources)
        if file_lines is None:
            continue
        shingles = _row_shingles(r, file_lines, min_lines)
        if shingles is not None:
            out.append((r, shingles))
    return out


def _owners_by_shingle(indexed: list[tuple[InventoryRow, set[int]]]) -> dict[int, int | list[int]]:
    """Inverted shingle index: a lone owner stays a bare int, a list starts at two.

    84% of shingles in a real repo have exactly one owner (measured: 1,210,103
    of 1,443,450) and can never produce a pair, so a singleton never costs a
    one-element list object; 1.21M of those were 77 MB of pure overhead.
    """
    by_shingle: dict[int, int | list[int]] = {}
    for idx, (_, shingles) in enumerate(indexed):
        for s in shingles:
            prev = by_shingle.get(s)
            if prev is None:
                by_shingle[s] = idx
            elif type(prev) is int:
                by_shingle[s] = [prev, idx]
            else:
                prev.append(idx)
    return by_shingle


def _pairable_owners(indexed: list[tuple[InventoryRow, set[int]]]) -> list[list[int]]:
    """Only the shingles two or more functions share. Keeping just these lets
    the whole index go before the pair counting below allocates anything."""
    return [o for o in _owners_by_shingle(indexed).values() if type(o) is list]


def _shared_counts(indexed: list[tuple[InventoryRow, set[int]]]) -> dict[tuple[int, int], int]:
    shared: dict[tuple[int, int], int] = {}
    for owners in _pairable_owners(indexed):
        for i, a in enumerate(owners):
            for b in owners[i + 1:]:
                shared[(a, b)] = shared.get((a, b), 0) + 1
    return shared


def _pair_payload(a: InventoryRow, b: InventoryRow, similarity: float) -> dict:
    functions = sorted(({"path": r.path, "long_name": r.long_name, "start": r.start,
                         "end": r.end, "nloc": r.nloc} for r in (a, b)),
                       key=lambda f: (f["path"], f["start"]))
    return {"functions": functions, "similarity": round(similarity, 4)}


def _twin_payload(r: InventoryRow, similarity: float, contained: bool) -> dict:
    return {"path": r.path, "long_name": r.long_name, "start": r.start,
            "end": r.end, "nloc": r.nloc, "similarity": round(similarity, 4),
            "contained": contained}


def _encloses(outer, inner) -> bool:
    return outer.start <= inner.start and inner.end <= outer.end


def _nested_spans(a, b) -> bool:
    """One span inside the other: nesting, not a clone.

    A nested function's normalized lines are a subset of its enclosing
    function's, so the pair scores 100% and reads as a perfect duplicate that
    nobody can deduplicate. Only meaningful inside one file — the line numbers
    of two different files never nest.
    """
    return a.path == b.path and (_encloses(a, b) or _encloses(b, a))


def _is_self(r: InventoryRow, target) -> bool:
    """Path plus start line: no two functions in a file open on the same line."""
    return r.path == target.path and r.start == target.start


def _target_shingles(target, sources: dict[str, str], min_lines: int) -> set[int] | None:
    lines = _split_once(target.path, sources)
    return None if lines is None else _row_shingles(target, lines, min_lines)


def _twin_scores(mine: set[int], target, rows: list[InventoryRow],
                 sources: dict[str, str], min_lines: int) -> list[dict]:
    return [_twin_payload(r, len(mine & other) / min(len(mine), len(other)),
                          _nested_spans(r, target))
            for r, other in _function_shingles(rows, sources, min_lines)
            if not _is_self(r, target)]


def find_twins(target, rows: list[InventoryRow], sources: dict[str, str], *,
               min_lines: int = 8, similarity: float = 0.8, top: int = 10) -> list[dict]:
    """The near-duplicates of ONE function, scored exactly as find_duplicates
    scores the pair it would appear in.

    One function's shingles against every other function's, so a brief costs a
    single row's comparisons instead of the whole repo's pair counting.
    """
    mine = _target_shingles(target, sources, min_lines)
    if not mine:
        return []
    kept = [t for t in _twin_scores(mine, target, rows, sources, min_lines)
            if t["similarity"] >= similarity]
    kept.sort(key=lambda t: (-t["similarity"], t["path"], t["start"]))
    return kept[:top]


def find_duplicates(rows: list[InventoryRow], load_sources, *,
                    min_lines: int = 8, similarity: float = 0.8,
                    top: int = 50) -> list[dict]:
    """Every near-duplicate pair in a snapshot, best containment first.

    `load_sources` is a CALLABLE returning {path: text}, not the texts
    themselves. Shingles are ints: a file's text is dead the moment its rows are
    indexed, and the pair counting below is where the heap actually goes. A
    caller that bound those texts to a name would pin all of them across it —
    measured 523 MB peak on a 104 MB repo against 377 MB with them released.
    Passing the loader's dict straight into _function_shingles is what releases
    it: nothing here ever holds a reference, so the index outlives the texts.
    """
    indexed = _function_shingles(rows, load_sources(), min_lines)
    pairs = []
    for (ia, ib), count in _shared_counts(indexed).items():
        a, b = indexed[ia], indexed[ib]
        score = count / min(len(a[1]), len(b[1]))
        if score >= similarity:
            pairs.append(_pair_payload(a[0], b[0], score))
    pairs.sort(key=lambda p: (-p["similarity"], p["functions"][0]["path"],
                              p["functions"][0]["start"]))
    return pairs[:top]
