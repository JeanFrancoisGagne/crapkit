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

A test asserts all three are empty. A function tree-sitter could not parse
(an ERROR node inside it, often a macro the grammar does not know) and a crapkit
row that starts inside an ERROR node are not judged; they are counted under
PARSE_ERROR. No crapkit import.
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
PARSE_ERROR = "tree-sitter parse error"


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


def file_context(path: str, data: bytes):
    """One file's parse, or None for a suffix no spec reads."""
    language = counters.language_of(path)
    if language is None:
        return None
    spec = counters.SPECS[language]
    return ts_defect_shapes.Context(language, spec, data, counters.parse(language, data))


def functions(path: str, data: bytes, context=None) -> list:
    """(function node, spec, file context) for each function of one file."""
    context = context or file_context(path, data)
    if context is None:
        return []
    return [(fn, context.spec, context) for fn in counters.functions(context.tree, context.spec)]


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
    rows, context = _rows_by_start(measured, path), file_context(path, data)
    for fn, spec, _ in functions(path, data, context):
        start = counters.start_line(fn)
        _compare_function(outcome, (path, start), fn, spec, context, rows.pop(start, None),
                          columns)
    for start, row in rows.items():
        _judge_extra(outcome, (path, start, row["long_name"]), context)


def _error_lines(context) -> set:
    """The lines ERROR nodes cover, cached per file."""
    if "errors" not in context.facts:
        context.facts["errors"] = {line for node in ts_defect_shapes.walk(context.tree.root_node)
                                   if node.type == "ERROR"
                                   for line in range(node.start_point[0] + 1,
                                                     node.end_point[0] + 2)}
    return context.facts["errors"]


def _judge_extra(outcome: Outcome, row: tuple, context) -> None:
    if context is not None and row[1] in _error_lines(context):
        outcome.set_aside[PARSE_ERROR] += 1
    elif not _explained(context, row[1]):
        outcome.extra.append(row)


def _explained(context, start: int) -> bool:
    return context is not None and ts_defect_shapes.extra_explained(context, start)


def _inside(fn, kinds) -> bool:
    parent = fn.parent
    while parent is not None and parent.type not in kinds:
        parent = parent.parent
    return parent is not None


def _artifact(fn, context) -> bool:
    """A C-family function_definition inside another function, inside an ERROR node or
    with no name: C has no nested functions, so the grammar's error recovery made it."""
    return fn.type == "function_definition" and (
        _inside(fn, context.spec.functions | {"ERROR"}) or not counters.name(fn, context.data))


def _unreadable(fn, context) -> bool:
    """A function tree-sitter could not parse, or one its error recovery made up."""
    return fn.has_error or _artifact(fn, context)


def reasons(fn, context, columns) -> list:
    """The rulings ids (or PARSE_ERROR) that set fn aside for `columns`; empty to compare."""
    if _unreadable(fn, context):
        return [PARSE_ERROR]
    return ts_defect_shapes.reasons(fn, context, columns)


def _compare_function(outcome, where, fn, spec, context, row, columns) -> None:
    held = reasons(fn, context, columns)
    if held:
        outcome.set_aside["+".join(held)] += 1
        return
    expected = values(fn, spec, context.data)
    if row is None:
        outcome.missing.append((*where, expected["name"]))
        return
    outcome.compared += 1
    outcome.differing.extend((*where, expected["name"], column, row[column], expected[column])
                             for column in columns if row[column] != expected[column])
