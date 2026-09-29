"""Near-duplicate containment worked outside crapkit: the stated line scheme, a
brute-force pass over every pair, and pylint 4.0.9's symilar.

The scheme (the packet's hand values; docs/agent-json.md names only "shared
shingles over the smaller function", a doc gap): a function's own lines,
its span less every line past the first of a function nested in it (README
`duplication` row), each with every whitespace character removed, blank lines
and comment lines left out, cut into windows of 4 consecutive lines. Containment is
the windows two functions share over the smaller function's window count.
Python's comment lines start with `#`; a line that opens or closes a docstring
(its text starts with three quotes) is left out as crapkit leaves it out, the
convention the README `duplication` row states (AO-DUP-DOCSTRING-LINES).

crapkit left out a Python code line whose text starts with `*`, `//` or `/*`
until calc-bug analysis-oracles-140 was fixed. The scheme keeps it; a function
holding one is still set aside and counted, never compared.

symilar (pylint 4.0.9, `python -m pylint.checkers.symilar`) reports the runs
of equal lines two files share. Five transforms turn its runs into the
scheme's containment, each a rulings row with a hand case:
- AO-SYMILAR-SCHEME: symilar strips only a line's ends and keeps comment
  lines, so it reads each function's scheme lines, not its source.
- AO-SYMILAR-ONE-FILE: symilar never compares a file with itself, so each
  function's lines go in a file of their own.
- AO-SYMILAR-CONTENT-LINES: symilar counts only lines with a word character
  toward a run's size (filter_noncode_lines), so every line is written with a
  leading `x`; equal lines stay equal.
- AO-SYMILAR-THRESHOLD: symilar keeps a run of more than --duplicates lines, so
  it runs at 3 and a run of 4 lines, one shared window, is kept.
- AO-SYMILAR-EVERY-COUPLE: the printed report drops a couple whose block
  another couple of the same size already names, so the adapter reads every
  couple symilar finds (Symilar._iter_sims, what the report is built from).
The windows inside a couple's run, read on its first file, are the pair's
shared windows. One symilar process reads a whole file list.

No crapkit import.
"""
from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
import re
import sys

import hang_guard

WINDOW = 4
MIN_LINES = 8  # duplication's --min-lines default (README)
LEFT_OUT = ("#", '"""', "'''")
CODE_PREFIXES = ("*", "//", "/*")


def normalized(lines: list[str], start: int, end: int,
               taken: frozenset[int] = frozenset()) -> list[str]:
    """Lines start..end (1-based, inclusive) as the scheme reads them, less the
    line numbers in `taken`."""
    kept = (raw for number, raw in enumerate(lines[start - 1:end], start)
            if _kept(number, raw, taken))
    return ["".join(raw.split()) for raw in kept]


def _kept(number: int, raw: str, taken: frozenset[int]) -> bool:
    """A line the scheme reads: no nested function's, not blank, not left out."""
    return number not in taken and bool(raw.strip()) and not raw.strip().startswith(LEFT_OUT)


def _nested_in(inner: dict, outer: dict) -> bool:
    """inner is another function inside outer's span: a shorter span in one file."""
    return (inner["path"] == outer["path"] and _inside(inner, outer)
            and (inner["start"], inner["end"]) != (outer["start"], outer["end"]))


def taken_lines(row: dict, file_rows: list[dict]) -> frozenset[int]:
    """Every line past the first of a function nested in `row`: that function's own."""
    return frozenset(number for inner in file_rows if _nested_in(inner, row)
                     for number in range(inner["start"] + 1, inner["end"] + 1))


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


def kept_functions(rows: list[dict], texts: dict[str, list[str]], min_lines: int = MIN_LINES):
    """(row, kept lines) for each function whose own kept lines reach min_lines."""
    by_path: dict[str, list[dict]] = {}
    for row in rows:
        by_path.setdefault(row["path"], []).append(row)
    for row in rows:
        taken = taken_lines(row, by_path[row["path"]])
        kept = normalized(texts[row["path"]], row["start"], row["end"], taken)
        if len(kept) >= min_lines:
            yield row, kept


def shingled(rows: list[dict], texts: dict[str, list[str]], min_lines: int = MIN_LINES):
    """(row, windows) for each function whose kept lines reach min_lines."""
    return ((row, windows(kept)) for row, kept in kept_functions(rows, texts, min_lines))


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


# --- symilar ---------------------------------------------------------------------------------

CONTENT_PREFIX = "x"  # AO-SYMILAR-CONTENT-LINES
SYMILAR_LINES = 3  # AO-SYMILAR-THRESHOLD
# Every couple Symilar._iter_sims yields for a file list, as JSON lists of
# [first file, start, end, second file, start, end] (0-based, end excluded).
CHILD = """
import json, sys
from pylint.checkers.symilar import Symilar
runner = Symilar(min_lines=int(sys.argv[2]))
for path in json.loads(open(sys.argv[1], encoding="utf-8").read()):
    with open(path, encoding="utf-8") as stream:
        runner.append_stream(path, stream)
json.dump([[c.fst_lset.name, c.fst_file_start, c.fst_file_end, c.snd_lset.name,
            c.snd_file_start, c.snd_file_end] for c in runner._iter_sims()], sys.stdout)
"""
SIMILAR = re.compile(r"^\d+ similar lines in \d+ files$")
ENTRY = re.compile(r"^==(.+):\[(\d+):(\d+)\]$")


def _ran(argv: list[str], cwd: Path) -> str:
    done = hang_guard.run([sys.executable, *argv], cwd=cwd, text=True, encoding="utf-8",
                          errors="replace")
    if done.returncode:
        raise RuntimeError(f"symilar failed (exit {done.returncode}): {done.stderr[-2000:]}")
    return done.stdout


def couples(names: list[str], cwd: Path, min_lines: int = SYMILAR_LINES) -> list[list]:
    """Every couple symilar finds among the files `names` (relative to cwd)."""
    listing = cwd / "symilar-files.json"
    listing.write_text(json.dumps(names), encoding="utf-8")
    return json.loads(_ran(["-c", CHILD, str(listing), str(min_lines)], cwd))


def report(names: list[str], cwd: Path, min_lines: int = 4) -> list[list[tuple]]:
    """The couples symilar's printed report names, each as its (file, start, end) entries."""
    found: list[list[tuple]] = []
    text = _ran(["-m", "pylint.checkers.symilar", "-d", str(min_lines), *names], cwd)
    for line in text.splitlines():
        entry = ENTRY.match(line)
        if SIMILAR.match(line):
            found.append([])
        elif entry and found:
            found[-1].append((entry[1], int(entry[2]), int(entry[3])))
    return found


def function_files(rows: list[dict], texts: dict[str, list[str]], work: Path,
                   prefix: str = CONTENT_PREFIX) -> dict[str, tuple[dict, list[str]]]:
    """Each shingled function's scheme lines, each led by `prefix`, in a file of
    its own under work: {file name: (row, lines as written)}."""
    work.mkdir(parents=True, exist_ok=True)
    written = {}
    for number, (row, kept) in enumerate(kept_functions(rows, texts)):
        name = f"f{number:05d}.txt"
        lines = [prefix + line for line in kept]
        (work / name).write_bytes("".join(line + "\n" for line in lines).encode("utf-8"))
        written[name] = (row, lines)
    return written


def _runs_by_pair(found: list[list]) -> dict[tuple[str, str], list[tuple[int, int]]]:
    grouped: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for first, start, end, second, _, _ in found:
        grouped.setdefault((first, second), []).append((start, end))
    return grouped


def shared_windows(lines: list[str], runs: list[tuple[int, int]]) -> set:
    """The windows inside symilar's runs, read on the first file's lines."""
    return {tuple(lines[at:at + WINDOW]) for start, end in runs
            for at in range(start, end - WINDOW + 1)}


def _score(written: dict, first: str, second: str, runs: list) -> tuple:
    (a, lines_a), (b, lines_b) = written[first], written[second]
    size = min(len(windows(lines_a)), len(windows(lines_b)))
    return a, b, len(shared_windows(lines_a, runs)) / size


def symilar_pairs(written: dict, found: list[list], similarity: float) -> dict:
    """{(key, key): containment at 4 places} from symilar's couples over the
    function files `written`, for pairs that reach `similarity` and do not nest."""
    pairs = {}
    for (first, second), runs in _runs_by_pair(found).items():
        a, b, score = _score(written, first, second, runs)
        if score >= similarity and not nested(a, b):
            pairs[pair_key(a, b)] = round(score, 4)
    return pairs


def pairs(rows: list[dict], texts: dict[str, list[str]], work: Path, similarity: float) -> dict:
    """The whole adapter: function files, one symilar process, containment."""
    written = function_files(rows, texts, work)
    return symilar_pairs(written, couples(sorted(written), work), similarity)
