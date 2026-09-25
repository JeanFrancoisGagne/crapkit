"""One loop for every tree-sitter differential: crapkit's rows against the
tree-sitter counters (oracles/treesitter_*), function by function.

compare() lists each file's functions with treesitter_counters, joins each to
crapkit's row by path and start line, and compares the named columns. A
function a known shape covers (ts_defect_shapes) is set aside for the columns
its rulings row names, and counted. What is left is:

- `missing`: a function the oracle lists and crapkit has no row for;
- `extra`: a crapkit row at a line where the oracle lists no function;
- `differing`: (path, start, name, column, crapkit, oracle) for each column
  that disagrees.

A test asserts all three are empty. No crapkit import.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from accuracy.analysis_oracles import ts_defect_shapes
from accuracy.analysis_oracles.oracles import treesitter_cognitive as cognitive
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.analysis_oracles.oracles import treesitter_depth as depth

SPAN = ("end",)
COUNTS = ("ccn_std", "ccn_mod")
SIZES = ("nloc", "params")
COGNITIVE = ("cognitive",)
NESTING = ("nesting",)


@dataclass
class Outcome:
    compared: int = 0
    set_aside: Counter = field(default_factory=Counter)
    missing: list = field(default_factory=list)
    extra: list = field(default_factory=list)
    differing: list = field(default_factory=list)

    @property
    def problems(self) -> list:
        return self.missing + self.extra + self.differing


def values(fn, spec, data: bytes) -> dict:
    """Every column the oracles give one function."""
    return {"name": counters.name(fn, data), "start": counters.start_line(fn),
            "end": counters.end_line(fn), "params": counters.params(fn, spec, data),
            "nloc": counters.nloc(fn, spec), "ccn_std": counters.ccn(fn, spec, data),
            "ccn_mod": counters.ccn(fn, spec, data, mod=True),
            "cognitive": cognitive.cognitive(fn, spec, data), "nesting": depth.depth(fn, spec)}


def functions(path: str, data: bytes) -> list:
    """(function node, spec, file context) for each function of one file."""
    language = counters.language_of(path)
    if language is None:
        return []
    spec, tree = counters.SPECS[language], counters.parse(language, data)
    context = ts_defect_shapes.Context(language, spec, data, tree)
    return [(fn, spec, context) for fn in counters.functions(tree, spec)]


def _rows_by_start(measured, path: str) -> dict:
    rows: dict = {}
    for row in measured.in_file(path):
        rows.setdefault(row["start"], row)
    return rows


def compare(files: dict, measured, columns: tuple, outcome: Outcome | None = None) -> Outcome:
    outcome = outcome or Outcome()
    for path, data in files.items():
        _compare_file(outcome, path, data, measured, columns)
    return outcome


def _compare_file(outcome: Outcome, path: str, data: bytes, measured, columns: tuple) -> None:
    rows, found = _rows_by_start(measured, path), functions(path, data)
    for fn, spec, context in found:
        start = counters.start_line(fn)
        _compare_function(outcome, (path, start), fn, spec, context, rows.pop(start, None),
                          columns)
    context = found[0][2] if found else None
    outcome.extra.extend((path, start, row["long_name"]) for start, row in rows.items()
                         if not _explained(context, start))


def _explained(context, start: int) -> bool:
    return context is not None and ts_defect_shapes.extra_explained(context, start)


def _compare_function(outcome, where, fn, spec, context, row, columns) -> None:
    reasons = ts_defect_shapes.reasons(fn, context, columns)
    if reasons:
        outcome.set_aside["+".join(reasons)] += 1
        return
    expected = values(fn, spec, context.data)
    if row is None:
        outcome.missing.append((*where, expected["name"]))
        return
    outcome.compared += 1
    outcome.differing.extend((*where, expected["name"], column, row[column], expected[column])
                             for column in columns if row[column] != expected[column])
