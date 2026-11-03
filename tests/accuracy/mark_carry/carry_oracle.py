"""Which mark judges a function, from docs/ratchet.md's words and lizard's names.

The rule ("A signature change keeps its mark"): a function's own key judges it
when that key holds a mark. Otherwise a mark carries to it when the mark's key
left the file, it is the only marked key of the function's bare name that did,
and the function is the only unmarked function of that name; twins never carry.
A key is the long name lizard prints, then `#N` for the Nth function of that
long name in the file ("Twins: one name, several functions"). The bare name is
the long name before its parameter list. A mark judges by the usual rule: the
CRAP at 4 places sits at or under it.

A passing verify's tighten reads the same pairing from the mark's side
("How `verify` uses the ratchet"): a carried mark moves to its function's key,
and the ratchet check reads the function that carries it.

This module imports no crapkit module: the packet's tests hold the gate, the
tighten's counts and the ratchet check to it.
"""
from __future__ import annotations

from collections import Counter

import lizard


def long_names(source: str, filename: str = "grade.py") -> list[str]:
    """The long names lizard prints for SOURCE, in file order."""
    found = lizard.analyze_file.analyze_source_code(filename, source).function_list
    return [function.long_name for function in sorted(found, key=lambda f: f.start_line)]


def keyed(names: list[str]) -> list[str]:
    """Each function's key: its long name, `#N` added for the Nth of that name."""
    seen: Counter = Counter()
    keys = []
    for name in names:
        seen[name] += 1
        keys.append(name if seen[name] == 1 else f"{name}#{seen[name]}")
    return keys


def _ordinal(key: str) -> int:
    head, sep, tail = key.rpartition("#")
    return int(tail) if sep and tail.isdigit() else 1


def bare(key: str) -> str:
    """The name before the parameter list, a twin's `#N` cut first."""
    name = key.rpartition("#")[0] if _ordinal(key) > 1 else key
    return name.split("(")[0].strip()


def judging_mark(keys: list[str], marks: dict[str, float], asked: str) -> float | str | None:
    """The mark that judges ASKED, "ambiguous" when the carry cannot say, None for none."""
    if asked in marks:
        return marks[asked]
    gone = [key for key in marks if key not in keys and bare(key) == bare(asked)]
    came = [key for key in keys if key not in marks and bare(key) == bare(asked)]
    if not gone:
        return None
    if len(gone) > 1 or len(came) > 1 or any(_ordinal(key) > 1 for key in gone + came):
        return "ambiguous"
    return marks[gone[0]]


def answer(keys: list[str], marks: dict[str, float], asked: str, crap: float) -> str:
    """pass, refuse or ambiguous for ASKED over its ceiling at CRAP."""
    mark = judging_mark(keys, marks, asked)
    if mark == "ambiguous":
        return "ambiguous"
    return "pass" if mark is not None and round(crap, 4) <= mark else "refuse"


def carrier(keys: list[str], marks: dict[str, float], mark: str) -> str | None:
    """The one function MARK carries to by the rule above, None when none can
    say: the same pairing asked from the mark's side."""
    if mark in keys or not bare(mark):  # an anonymous function never carries
        return None
    came = [key for key in keys if key not in marks and bare(key) == bare(mark)]
    gone = [key for key in marks if key not in keys and bare(key) == bare(mark)]
    if len(gone) != 1 or len(came) != 1 or any(_ordinal(key) > 1 for key in gone + came):
        return None
    return came[0]


def updated(keys: list[str], craps: dict[str, float], marks: dict[str, float],
            ceiling: int) -> dict[str, float]:
    """The marks after a passing verify ("How `verify` uses the ratchet"): a
    mark judges its own function or its carrier; at or under the ceiling it
    leaves, over it the lower of mark and fresh CRAP stays under the
    function's key; a mark no function holds stays as it is."""
    after = {}
    for mark, value in marks.items():
        key = mark if mark in keys else carrier(keys, marks, mark)
        if key is None:
            after[mark] = value
        elif round(craps[key], 4) > ceiling:
            after[key] = min(value, round(craps[key], 4))
    return after


def read_marks(text: str) -> dict[str, float]:
    """A one-path marks file's key names and marks, line by line: comment
    lines and the header carry no mark."""
    rows = [line.split("\t") for line in text.splitlines() if line and not line.startswith("#")]
    return {name: float(value) for _, name, value in rows[1:]}


def counts(prior_text: str, updated_text: str, keys: list[str]) -> tuple[int, int, int]:
    """(dropped, tightened, rekeyed) from the two files' text: a mark whose
    line is gone is re-keyed when its carrier's line is there, else dropped;
    one that fell, under either key, is tightened."""
    before, after = read_marks(prior_text), read_marks(updated_text)
    dropped = tightened = rekeyed = 0
    for mark, value in before.items():
        moved = None if mark in after else carrier(keys, before, mark)
        now = after.get(mark if moved is None else moved)
        dropped += now is None
        rekeyed += now is not None and moved is not None
        tightened += now is not None and now < value
    return dropped, tightened, rekeyed


def rises(keys: list[str], craps: dict[str, float], marks: dict[str, float]) -> list[tuple[str, str | None]]:
    """(function key, the mark's key when it carried) for each mark the
    function it judges rose past."""
    found = []
    for mark, value in marks.items():
        key = mark if mark in keys else carrier(keys, marks, mark)
        if key is not None and round(craps[key], 4) > value:
            found.append((key, None if key == mark else mark))
    return found


def exact_key_answer(marks: dict[str, float], asked: str, crap: float) -> str:
    """0.8.1's rule, a mark judging only its own key: the retro check's verdict."""
    mark = marks.get(asked)
    return "pass" if mark is not None and round(crap, 4) <= mark else "refuse"
