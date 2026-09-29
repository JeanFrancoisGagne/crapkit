"""One loop for every Python differential: crapkit's column against an outside
count, def by def, over a corpus crapkit measured.

compare() walks every def of every file, sets aside the defs a detector table
(py_defect_shapes) flags, each under its rulings id, and returns what is left
that disagrees plus how many defs each id set aside. A test asserts the first
is empty and writes the second to the run log. No crapkit import.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field

from accuracy.analysis_oracles import py_defect_shapes

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass
class Outcome:
    compared: int = 0
    set_aside: Counter = field(default_factory=Counter)
    differing: list = field(default_factory=list)


def _text(data) -> str:
    return data.decode("utf-8") if isinstance(data, bytes) else data


def defs(source: str) -> list[tuple]:
    """(def node, whether it sits inside another def) for every def."""
    tree = ast.parse(source)
    nested = py_defect_shapes.nested_defs(tree)
    return [(node, id(node) in nested) for node in ast.walk(tree) if isinstance(node, FUNCTIONS)]


def _reasons(fn, nested: bool, detectors: dict) -> list[str]:
    return py_defect_shapes.reasons(fn, detectors, nested) if detectors else []


def _row(measured, path: str, fn) -> dict | None:
    return next((row for row in measured.in_file(path) if row["start"] == fn.lineno), None)


def _judge(outcome: Outcome, where: tuple, crapkit, expected) -> None:
    outcome.compared += 1
    if crapkit != expected:
        outcome.differing.append((*where, crapkit, expected))


def compare(files: dict, measured, column: str, expected, detectors: dict | None = None,
            outcome: Outcome | None = None) -> Outcome:
    """crapkit's `column` for every def against expected(fn, source, path),
    which answers None for a def the oracle does not read (counted as such)."""
    outcome = outcome or Outcome()
    for path, data in files.items():
        source = _text(data)
        for fn, nested in defs(source):
            _one(outcome, (path, fn.lineno, fn.name), fn, nested, source, measured, column,
                 expected, detectors or {})
    return outcome


def _one(outcome, where, fn, nested, source, measured, column, expected, detectors) -> None:
    reasons = _reasons(fn, nested, detectors)
    if reasons:
        outcome.set_aside["+".join(reasons)] += 1
        return
    value = expected(fn, source, where[0])
    if value is None:
        outcome.set_aside["not read by the oracle"] += 1
        return
    row = _row(measured, where[0], fn)
    _judge(outcome, where, None if row is None else row[column], value)
