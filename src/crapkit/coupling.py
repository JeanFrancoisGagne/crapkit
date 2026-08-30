"""Change coupling from the churn git log. Pure.

Files that keep landing in the same commits are coupled, whatever the import
graph says — the hidden dependency the compiler cannot show. Bulk commits are
skipped for pairing: a 40-file sweep says nothing about any particular pair.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from itertools import combinations

from .churn import _unquote_git_path

MAX_COMMIT_FILES = 30
# The thresholds the ranking answers when nobody names one. Named because
# coupling_cache stores the ranking at exactly these, and cli/analyses has to
# recognize a `coupling` invocation that asked for something wider.
DEFAULT_MIN_SUPPORT = 5
DEFAULT_MIN_CONFIDENCE = 0.5


def _commit_file_sets(lines: Iterable[str]) -> Iterator[set[str]]:
    """One file set per commit, yielded as the log streams past.

    A %x01 line opens a commit and every non-blank line until the next one is a
    path. Empty sets are yielded rather than skipped: they add no file counts
    and form no pairs, so the caller cannot tell them from a skip.

    Unquoted before the slashes are normalized, the way the churn parser does
    it: git quotes a non-ASCII path and writes its bytes as octal escapes, so
    normalizing first cuts `src/b\\303\\252ta.py` into directories. Either way
    the pair would name a path no repo has, and the file the log meant would
    never join the churn map, ls-files or a scored row.
    """
    files: set[str] = set()
    past_header = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("\x01"):
            yield files
            files, past_header = set(), True
            continue
        if past_header and line:
            files.add(_unquote_git_path(line).replace("\\", "/"))
        past_header = True  # a log starting mid-commit opens on a severed header
    yield files


def _tracked_pairs(pair_counts: dict, tracked: set[str] | None) -> dict:
    """The pairs both of whose files git still has, or all of them when the
    caller cannot say.

    A file keeps its history under its old name: rename it and the log pairs
    the dead name for another year, at full support and full confidence. That
    pair outranks the live one and names a file the agent cannot open. Dropped
    here rather than after the cut, so a dead pair does not spend a slot.
    """
    if tracked is None:
        return pair_counts
    return {(a, b): n for (a, b), n in pair_counts.items() if a in tracked and b in tracked}


def _rank_pairs(file_counts: dict, pair_counts: dict, min_support: int,
                min_confidence: float, top: int | None) -> list[dict]:
    out = []
    for (a, b), support in pair_counts.items():
        if support < min_support:
            continue
        confidence = max(support / file_counts[a], support / file_counts[b])
        if confidence < min_confidence:
            continue
        out.append({"files": [a, b], "support": support, "confidence": round(confidence, 4)})
    out.sort(key=lambda p: (-p["support"] * p["confidence"], p["files"]))
    return out if top is None else out[:top]


def _partner(pair: dict, path: str) -> dict:
    a, b = pair["files"]
    return {"path": b if a == path else a, "support": pair["support"],
            "confidence": pair["confidence"]}


def partners(lines: Iterable[str], path: str, *, min_support: int = DEFAULT_MIN_SUPPORT,
             min_confidence: float = DEFAULT_MIN_CONFIDENCE, top: int = 5) -> list[dict]:
    """One file's coupled partners, best first.

    Ranked over EVERY qualifying pair before the cut: a global top applied first
    would drop a quiet file's own partners behind the repo's noisiest pairs and
    report it as uncoupled.
    """
    ranked = change_coupling_lines(lines, min_support=min_support,
                                   min_confidence=min_confidence, top=None)
    return [_partner(p, path) for p in ranked if path in p["files"]][:top]


def change_coupling(log_text: str, *, min_support: int = DEFAULT_MIN_SUPPORT,
                    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
                    top: int | None = 50, tracked: set[str] | None = None) -> list[dict]:
    """Whole-text entrypoint: the log already in hand."""
    return change_coupling_lines(log_text.splitlines(), min_support=min_support,
                                 min_confidence=min_confidence, top=top, tracked=tracked)


def change_coupling_lines(lines: Iterable[str], *, min_support: int = DEFAULT_MIN_SUPPORT,
                          min_confidence: float = DEFAULT_MIN_CONFIDENCE,
                          top: int | None = 50,
                          tracked: set[str] | None = None) -> list[dict]:
    """Streaming entrypoint: the log is consumed once, one commit at a time.

    `tracked` is the repo's live path set — `git ls-files` — when the caller has
    one to give; None keeps every pair the history holds.
    """
    commits = _commit_file_sets(lines)
    file_counts: dict[str, int] = {}
    pair_counts: dict[tuple[str, str], int] = {}
    for files in commits:
        for f in files:
            file_counts[f] = file_counts.get(f, 0) + 1
        if len(files) > MAX_COMMIT_FILES:
            continue
        for pair in combinations(sorted(files), 2):
            pair_counts[pair] = pair_counts.get(pair, 0) + 1
    return _rank_pairs(file_counts, _tracked_pairs(pair_counts, tracked),
                       min_support, min_confidence, top)
