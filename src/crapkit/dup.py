"""Near-duplicate function detection: inventory rows + file texts in, ranked
pairs out. Pure, except run_index: the one function here that touches the
store, reading a run's index back or building it and writing it there.

Normalized line shingles with CONTAINMENT scoring (shared / smaller set), so a
copy-paste that later grew a few lines still surfaces. An inverted shingle
index keeps a 14k-function repo tractable: only pairs that actually share a
shingle are ever compared. Tiny functions are structural noise and stay out.
Each function is shingled from its own lines: a function nested in it owns its
lines past its first, so a closure's clone pairs with the closure alone. A line
enters with its whitespace removed; blank lines and the comment lines of the
file's language stay out.

A shingle is a stable 8-byte digest, so one run's index can be stored and read
back by another process. Both readers take either kind of index: the
FunctionIndex built here, or the one the store keeps for a run. Each answers
two questions, each in one call so a stored answer comes from one version of
the index: which functions hold these shingles (`holders`), and every function
with every shingle two of them share (`pair_inputs`).
"""
from __future__ import annotations

from bisect import bisect_right
from hashlib import blake2b
import heapq
from itertools import chain
from posixpath import splitext
from typing import NamedTuple

from .keys import lookup
from .snapshot import InventoryRow
from .sourcelines import source_lines
from .universe import LANGUAGE_EXTENSIONS

WINDOW = 4  # consecutive normalized lines per shingle
# What a stored digest was made with. A stored index is only comparable with a
# target shingled the same way, so any change to the line split, _normalized_lines,
# _shingles or _digest changes this string, and every stored index reads as absent
# until the next build replaces it. v2: lines end where the reader ends them
# (sourcelines), no longer at a form feed, and a function is shingled from its own
# lines, its nested functions' left out. v3: comment lines as the file's language
# writes them.
SHINGLE_FORMAT = f"v3 blake2b-8 window {WINDOW}"


class _Comments(NamedTuple):
    """How one language writes a comment line.

    `line` holds the prefixes that make a whole line a comment. `block` opens a
    comment at a line's start and closes it wherever the closer falls, or is
    None in a language without one. `starts` is every prefix that sends a line
    down the slow path: the line prefixes and the block opener."""

    line: tuple[str, ...]
    block: tuple[str, str] | None
    starts: tuple[str, ...]


def _comments(line: tuple[str, ...], block: tuple[str, str] | None = None) -> _Comments:
    return _Comments(line, block, line + (block[:1] if block else ()))


# A comment is what the language's reference calls one, so a Python `// 2` or
# `**kwargs` line, a C `*out = x;` or `#define` line and a Rust `#[attr]` line
# are code. Python's docstrings are string literals; a line that opens or
# closes one is left out by convention, as it always was.
_C_FAMILY = _comments(("//",), ("/*", "*/"))
_COMMENTS = {"python": _comments(("#", '"""', "'''")), "shell": _comments(("#",)),
             "powershell": _comments(("#",), ("<#", "#>")), "zig": _comments(("//",))}
_COMMENTS_BY_SUFFIX = {suffix: _COMMENTS.get(language, _C_FAMILY)
                       for language, suffixes in LANGUAGE_EXTENSIONS.items() for suffix in suffixes}
# The one threshold a run's index is stored at: brief's, and duplication's
# default. Any other min_lines builds its own index for that call.
STORED_MIN_LINES = 8


class _Source(NamedTuple):
    """One file as the shingler reads it: its lines, the span of every function
    in it as a `_span_key`, sorted, and how its language writes a comment."""

    lines: list[str]
    spans: list[tuple[int, int, int]]
    comments: _Comments


def _span_key(r) -> tuple[int, int, int]:
    """Sorts a function before every function nested in it: by start line, the
    longer span first, and on one span the earlier occurrence first."""
    return r.start, -r.end, r.occurrence


def _spans_by_path(rows) -> dict[str, list[tuple[int, int, int]]]:
    spans: dict[str, set] = {}
    for r in rows:
        spans.setdefault(r.path, set()).add(_span_key(r))
    return {path: sorted(keys) for path, keys in spans.items()}


def _nested_in(r, spans: list[tuple[int, int, int]]):
    """(start, end) of every function nested in r, by start line.

    A later occurrence on r's own span is nested in it too: an arrow that
    returns an arrow, `f = () => () => {`, is one span to lizard, the outer
    arrow first. Two scopes scoring one file give one function two rows with
    one key, and neither is nested in the other."""
    for at in range(bisect_right(spans, _span_key(r)), len(spans)):
        start, neg_end, _ = spans[at]
        if start > r.end:
            return
        if -neg_end <= r.end:
            yield start, -neg_end


def _own_runs(r, spans: list[tuple[int, int, int]]):
    """r's own lines as runs of (first, last) line numbers.

    A nested function's lines past its first are its own, not r's: that first
    line holds r's `def` or call around it, and lizard's nloc counts it for
    both. Without the cut a factory carried its closure's body, and every clone
    of the closure paired twice, once through the factory."""
    at = r.start
    for start, end in _nested_in(r, spans):
        if start >= at:
            yield at, start
        at = max(at, end + 1)
    yield at, r.end


def _opening(text: str, comments: _Comments) -> tuple[str | None, int]:
    """The closer a stripped line awaits when it opens a block comment, and
    where the search for it starts; (None, 0) when it opens none."""
    block = comments.block
    if block and text.startswith(block[0]):
        return block[1], len(block[0])
    return None, 0


def _uncommented(text: str, closer: str | None, comments: _Comments) -> tuple[str, str | None]:
    """What of a stripped line is code, and the block closer still awaited
    after it. A line holding code after a closed block comment is code. One
    loop turn per block comment closed on the line, so no line is too long."""
    start = 0
    if closer is None:
        closer, start = _opening(text, comments)
    while closer is not None:
        at = text.find(closer, start)
        if at < 0:
            return "", closer
        text = text[at + len(closer):].lstrip()
        closer, start = _opening(text, comments)
    return ("" if text.startswith(comments.line) else text), None


def _code_lines(raw_lines, comments: _Comments) -> list[str]:
    """Each code line with its whitespace removed, blank and comment lines left
    out. Only a line inside a block comment or starting like a comment pays
    for the closer and prefix checks."""
    picked, closer = [], None
    for raw in raw_lines:
        text = raw.strip()
        if closer is not None or text.startswith(comments.starts):
            text, closer = _uncommented(text, closer, comments)
        if text:
            picked.append("".join(raw.split()))  # whitespace never distinguishes a clone
    return picked


def _normalized_lines(source: _Source, r) -> list[str]:
    runs = _own_runs(r, source.spans)
    return _code_lines(chain.from_iterable(source.lines[first - 1:last] for first, last in runs),
                       source.comments)


def _digest(window: bytes) -> int:
    """A window's 64-bit name, the same in every process and on every machine.

    Builtin hash() of the same tuple is salted per process: one index written
    and read back in three fresh interpreters shared 0 of 37 shingles. Signed
    so SQLite stores it as an INTEGER.
    """
    return int.from_bytes(blake2b(window, digest_size=8).digest(), "little", signed=True)


def _shingles(lines: list[str]) -> set[int]:
    # A normalized line holds no whitespace at all, so a newline cannot occur
    # inside one and joining on it keeps every window distinct.
    encoded = [line.encode("utf-8", "surrogatepass") for line in lines]
    return {_digest(b"\n".join(window))
            for window in zip(*(encoded[i:] for i in range(WINDOW)))}


def _source(path: str, text: str | None, spans: list[tuple[int, int, int]]) -> _Source | None:
    """The file's comment syntax is its language's, the C family's for a suffix
    no language claims."""
    if text is None:
        return None
    return _Source(source_lines(text), spans, _COMMENTS_BY_SUFFIX.get(splitext(path)[1], _C_FAMILY))


def _row_shingles(r: InventoryRow, source: _Source, min_lines: int) -> set[int] | None:
    lines = _normalized_lines(source, r)
    return _shingles(lines) if len(lines) >= min_lines else None


def _function_shingles(rows: list[InventoryRow], sources: dict[str, str],
                       min_lines: int) -> list[tuple[InventoryRow, set[int]]]:
    # Rows arrive ordered by (scope, path, start), so every row of a file is
    # contiguous: a ONE-ENTRY cache splits each source once instead of once per
    # function in it (measured 3 GB of re-split text on a 104 MB repo). A file
    # whose rows are NOT contiguous still scores identically, just re-split.
    out = []
    spans = _spans_by_path(rows)
    cached_path, source = None, None
    for r in rows:
        if r.path != cached_path:
            cached_path, source = r.path, _source(r.path, sources.get(r.path), spans[r.path])
        if source is None:
            continue
        shingles = _row_shingles(r, source, min_lines)
        if shingles is not None:
            out.append((r, shingles))
    return out


class FunctionIndex(NamedTuple):
    """Every shingled function of a snapshot, and the threshold it was built at.

    min_lines rides along because the index is only an answer at the threshold
    that produced it: a function too short at 8 is ABSENT from the entries, not
    scored low, so reading it at 4 would drop twins rather than report them.

    A function's number in every answer below is its place in `entries`, which
    is also the number the store files it under.
    """

    min_lines: int
    entries: list[tuple[InventoryRow, set[int]]]

    def holders(self, digests: set[int]) -> dict[int, tuple]:
        """Every function holding any of `digests`: its row, its shingle count,
        and how many of `digests` it holds."""
        return {fn: (row, len(shingles), n) for fn, (row, shingles) in enumerate(self.entries)
                if (n := len(digests & shingles))}

    def pair_inputs(self) -> tuple[list[tuple], list[list[int]]]:
        """Every function's row and shingle count, and the owners of every
        shingle two or more of them share."""
        return self.functions(), _pairable_owners(self.entries)

    def functions(self) -> list[tuple]:
        """Every function's row and shingle count, in function-number order."""
        return [(row, len(shingles)) for row, shingles in self.entries]

    def owners(self) -> dict[int, int | list[int]]:
        return _owners_by_shingle(self.entries)


def function_index(rows: list[InventoryRow], sources: dict[str, str],
                   min_lines: int = 8) -> FunctionIndex:
    """Shingle every function once, for callers that ask about many targets.

    `brief --batch N` scores N functions against the same snapshot, and building
    this per packet re-shingled the whole repo N times (measured -60% wall on a
    batch of 5 over a 31,459-file tree). The store keeps one of these per run
    for the same reason across processes: `SnapshotStore.twin_index` builds it
    on first ask and every later call reads it back.
    """
    return FunctionIndex(min_lines, _function_shingles(rows, sources, min_lines))


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


def _owner_groups(pairable: list[list[int]]) -> dict[int, list[list[int]]]:
    """Shared owner lists, referenced by every owner that has a later neighbor.

    Each list holds its owners in ascending function number, which is what lets
    _neighbor_counts cut a list at the owner with one bisect."""
    groups: dict[int, list[list[int]]] = {}
    for owners in pairable:
        for owner in owners[:-1]:
            groups.setdefault(owner, []).append(owners)
    return groups


def _neighbor_counts(owner: int, groups: list[list[int]]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for owners in groups:
        for neighbor in owners[bisect_right(owners, owner):]:
            counts[neighbor] = counts.get(neighbor, 0) + 1
    return counts


def _shared_counts(pairable: list[list[int]]):
    """One owner's neighbors at a time; never retain all function pairs."""
    for owner, groups in _owner_groups(pairable).items():
        for neighbor, count in _neighbor_counts(owner, groups).items():
            yield owner, neighbor, count


def _function_key(function: dict) -> tuple:
    return (function["path"], function["start"], function["end"],
            function["long_name"], function["nloc"])


def _pair_payload(a: InventoryRow, b: InventoryRow, similarity: float) -> dict:
    functions = sorted(({"path": r.path, "long_name": r.long_name, "start": r.start,
                         "end": r.end, "nloc": r.nloc} for r in (a, b)),
                       key=_function_key)
    # `contained` is False on every pair that gets here, because find_duplicates
    # drops the nested ones before building a payload. It is still emitted: a
    # consumer that reads pairs and twins together gets one shape, and False is
    # a claim about the spans rather than a key it has to guess the meaning of.
    return {"functions": functions, "similarity": round(similarity, 4),
            "contained": False}


def _twin_payload(r: InventoryRow, similarity: float, contained: bool) -> dict:
    return {"path": r.path, "long_name": r.long_name, "start": r.start,
            "end": r.end, "nloc": r.nloc, "similarity": round(similarity, 4),
            "contained": contained}


def _encloses(outer, inner) -> bool:
    return outer.start <= inner.start and inner.end <= outer.end


def _nested_spans(a, b) -> bool:
    """One span inside the other: nesting, not a clone.

    The two share shingles only where the enclosing function's own lines copy
    the nested one's, and nobody can deduplicate a function from the closure
    inside it. Only meaningful inside one file: the line numbers of two
    different files never nest.

    find_twins keeps such a pair and labels it, because a brief about one
    function wants to know its twin is the function around it or inside it.
    find_duplicates drops it.
    """
    return a.path == b.path and (_encloses(a, b) or _encloses(b, a))


def _is_self(r: InventoryRow, target) -> bool:
    """Scope copies share a location; separate same-line functions do not."""
    return lookup(r) == lookup(target)


def _target_shingles(target, text: str | None, file_rows, min_lines: int) -> set[int] | None:
    """`file_rows` hold the rows of the target's file. The functions nested in
    the target own their lines, so the target's shingles leave them out."""
    spans = _spans_by_path(r for r in file_rows if r.path == target.path)
    source = _source(target.path, text, spans.get(target.path, []))
    return None if source is None else _row_shingles(target, source, min_lines)


def _qualified_twins(size: int, target, holders: dict[int, tuple], similarity: float):
    """The raw containment meets the threshold, as in `_candidate`; only the
    similarity a twin reports is rounded."""
    for row, count, shared in holders.values():
        if _is_self(row, target):
            continue
        score = shared / min(size, count)
        if score >= similarity:
            yield _twin_payload(row, score, _nested_spans(row, target))


def _ranked_twins(index, target, mine: set[int], similarity: float, top: int) -> list[dict]:
    """`mine` scored against every function of `index` that holds any of it."""
    kept = list(_qualified_twins(len(mine), target, index.holders(mine), similarity))
    kept.sort(key=lambda t: (-t["similarity"], _function_key(t), t["contained"]))
    return kept[:top]


def _built_at(indexed, min_lines: int) -> bool:
    """A prebuilt index answers only at the threshold it was built at. Reusing
    it at another min_lines would silently lose the rows that threshold admits,
    so a mismatch pays for a rebuild."""
    return indexed is not None and indexed.min_lines == min_lines


def twins_in(index, target, text: str | None, file_rows, *, similarity: float = 0.8,
             top: int = 10) -> list[dict]:
    """ONE function's twins against a prebuilt index, the target shingled from `text`.

    `index` is a FunctionIndex or the store's index for a run. brief reads the
    stored one, so a packet shingles its own function and looks up the rest.
    `text` is the target's file as it reads now, or None when it is gone.
    `file_rows` are the run's rows of that file, the target among them: the
    functions nested in the target own their lines, here as in the index.
    """
    mine = _target_shingles(target, text, file_rows, index.min_lines)
    return _ranked_twins(index, target, mine, similarity, top) if mine else []


def find_twins(target, rows: list[InventoryRow], sources: dict[str, str], *,
               min_lines: int = 8, similarity: float = 0.8, top: int = 10,
               indexed=None) -> list[dict]:
    """The near-duplicates of ONE function, scored exactly as find_duplicates
    scores the pair it would appear in.

    One function's shingles against every other function's, so a brief costs a
    single row's comparisons instead of the whole repo's pair counting. Only a
    function that shares a shingle with the target is scored, as only such a
    pair reaches find_duplicates, so a similarity of 0 lists no function at 0.

    `indexed` is that other side, shingled once by function_index and reused
    across targets, or the store's index for the run. Passing it changes nothing
    about the answer; leaving it out builds the same thing for this call alone.
    """
    mine = _target_shingles(target, sources.get(target.path), rows, min_lines)
    if not mine:
        return []
    index = indexed if _built_at(indexed, min_lines) else function_index(rows, sources, min_lines)
    return _ranked_twins(index, target, mine, similarity, top)


class _Pair(NamedTuple):
    rank: tuple
    left: int
    right: int
    similarity: float


def _row_key(row: InventoryRow) -> tuple:
    return row.path, row.start, row.end, row.long_name, row.nloc


def _candidate(functions, keys, left: int, right: int, count: int, minimum: float) -> _Pair | None:
    a, b = functions[left], functions[right]
    score = count / min(a[1], b[1])
    if not (score >= minimum) or _nested_spans(a[0], b[0]):
        return None
    if keys[right] < keys[left]:
        left, right = right, left
    return _Pair((-round(score, 4), keys[left], keys[right]), left, right, score)


def _qualified_pairs(functions, pairable: list[list[int]], minimum: float):
    keys = [_row_key(row) for row, _ in functions]
    for left, right, count in _shared_counts(pairable):
        pair = _candidate(functions, keys, left, right, count, minimum)
        if pair is not None:
            yield pair


def _best_pairs(pairs, top: int) -> list[_Pair]:
    if top < 0:
        return sorted(pairs, key=lambda pair: pair.rank)[:top]
    return heapq.nsmallest(top, pairs, key=lambda pair: pair.rank)


def _pair_inputs(indexed, rows: list[InventoryRow], load_sources,
                 min_lines: int) -> tuple[list[tuple], list[list[int]]]:
    """Every function with its shingle count, and the owners of every shingle
    two or more of them share. An index built here is dropped on return, so no
    shingle set lives through the pair counting."""
    index = indexed if _built_at(indexed, min_lines) else \
        function_index(rows, load_sources(), min_lines)
    return index.pair_inputs()


def find_duplicates(rows: list[InventoryRow], load_sources, *,
                    min_lines: int = 8, similarity: float = 0.8,
                    top: int = 50, indexed=None) -> list[dict]:
    """Every near-duplicate pair in a snapshot, best containment first.

    A pair whose two spans nest is skipped: no one can deduplicate a factory
    from the closure defined inside it. On the repo that reported this, 43 of
    43 pairs were that shape, so the report was 100% noise before the skip.
    Those pairs scored 1.0 because the factory was shingled with its closure's
    lines; since each function keeps only its own, the factory no longer pairs
    through its closure with the closure's clones either.

    `load_sources` is a CALLABLE returning {path: text}, not the texts
    themselves. Shingles are ints: a file's text is dead the moment its rows are
    indexed, and the pair counting below is where the heap actually goes. A
    caller that bound those texts to a name would pin all of them across it —
    measured 523 MB peak on a 104 MB repo against 377 MB with them released.
    Passing the loader's dict straight into function_index is what releases
    it: nothing here ever holds a reference, so the index outlives the texts.

    `indexed` is the run's stored index, or any index built at `min_lines`: the
    pairs come off its owner lists and `load_sources` is never called. At any
    other threshold it is ignored and the index is built here.
    """
    functions, pairable = _pair_inputs(indexed, rows, load_sources, min_lines)
    selected = _best_pairs(_qualified_pairs(functions, pairable, similarity), top)
    return [_pair_payload(functions[pair.left][0], functions[pair.right][0], pair.similarity)
            for pair in selected]


def run_index(store, run_id: int, rows: list[InventoryRow], load_sources,
              min_lines: int = STORED_MIN_LINES):
    """The index `find_duplicates` reads for one run, or None to build its own.

    At the stored threshold it is the store's, built from `rows` and the texts
    `load_sources` returns and stored on first ask. At any other threshold it is
    None and the store is never touched: an index answers only at the min_lines
    it was built at, and the one the store keeps is the one brief reads.
    """
    if min_lines != STORED_MIN_LINES:
        return None
    return store.twin_index(run_id, lambda: function_index(rows, load_sources(), min_lines))
