"""Near-duplicate containment worked outside crapkit: the stated line scheme, a
brute-force pass over every pair, and pylint 4.0.9's symilar.

The scheme (the packet's hand values; docs/agent-json.md names only "shared
shingles over the smaller function", a doc gap): a function's lines from its
start to its end, each with every whitespace character removed, blank lines and
comment lines left out, cut into windows of 4 consecutive lines. Containment is
the windows two functions share over the smaller function's window count.
Python's comment lines start with `#`; a line that opens or closes a docstring
(its text starts with three quotes) is left out as crapkit leaves it out, a
convention the docs do not state (AO-DUP-DOCSTRING-LINES).

crapkit also leaves out a Python code line whose text starts with `*`, `//` or
`/*` (calc-bug analysis-oracles-140). The scheme keeps it; a function holding
one is set aside and counted, never compared.

No crapkit import.
"""
from __future__ import annotations

from itertools import combinations

WINDOW = 4
MIN_LINES = 8  # duplication's --min-lines default (README)
LEFT_OUT = ("#", '"""', "'''")
CODE_PREFIXES = ("*", "//", "/*")


def normalized(lines: list[str], start: int, end: int) -> list[str]:
    """Lines start..end (1-based, inclusive) as the scheme reads them."""
    kept = (raw for raw in lines[start - 1:end]
            if raw.strip() and not raw.strip().startswith(LEFT_OUT))
    return ["".join(raw.split()) for raw in kept]


def windows(kept: list[str]) -> set[tuple[str, ...]]:
    return {tuple(kept[at:at + WINDOW]) for at in range(len(kept) - WINDOW + 1)}


def holds_code_prefix(lines: list[str], start: int, end: int) -> bool:
    """A line crapkit leaves out that the scheme keeps (analysis-oracles-140)."""
    return any(raw.strip().startswith(CODE_PREFIXES) for raw in lines[start - 1:end])


def key(row: dict) -> tuple:
    return row["path"], row["start"], row["long_name"]


def set_aside(rows: list[dict], texts: dict[str, list[str]]) -> set[tuple]:
    """The keys of the functions holding an analysis-oracles-140 line."""
    return {key(row) for row in rows
            if holds_code_prefix(texts[row["path"]], row["start"], row["end"])}


def shingled(rows: list[dict], texts: dict[str, list[str]], min_lines: int = MIN_LINES):
    """(row, windows) for each function whose kept lines reach min_lines."""
    for row in rows:
        kept = normalized(texts[row["path"]], row["start"], row["end"])
        if len(kept) >= min_lines:
            yield row, windows(kept)


def _inside(inner: dict, outer: dict) -> bool:
    return outer["start"] <= inner["start"] and inner["end"] <= outer["end"]


def nested(a: dict, b: dict) -> bool:
    """README: a function and its nested closure never pair."""
    return a["path"] == b["path"] and (_inside(a, b) or _inside(b, a))


def containment(mine: set, theirs: set) -> float:
    return len(mine & theirs) / min(len(mine), len(theirs))


def pair_key(a: dict, b: dict) -> tuple:
    return tuple(sorted((key(a), key(b))))


def brute_force(rows: list[dict], texts: dict[str, list[str]], similarity: float) -> dict:
    """{(key, key): containment at 4 places} over every pair of shingled
    functions that share a window, reach `similarity` and do not nest."""
    found = {}
    for (a, mine), (b, theirs) in combinations(list(shingled(rows, texts)), 2):
        score = containment(mine, theirs)
        if score and score >= similarity and not nested(a, b):
            found[pair_key(a, b)] = round(score, 4)
    return found
