"""One loop for every outside-tool differential: crapkit's rows against a
tool that reports one column per function (gocyclo, rust-code-analysis,
Checkstyle and the rest).

Each function tree-sitter lists (analysis_treesitter) and the language's
filter keeps is joined to crapkit's row and the tool's answer by path and
start line. A function a known crapkit defect shape covers is set aside for
that column (ts_defect_shapes), as in the tree-sitter differential.

Where a tool counts a construct differently from crapkit's documented
reading, a named transform turns the tool's raw value into crapkit's, or the
function is set aside for that tool. Each such rule is a rulings row, and the
test module that declares the Tool pins every rule by a hand case with the
tool's raw value and crapkit's. No crapkit import.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from accuracy.analysis_oracles import analysis_treesitter
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.kit import runlog


@dataclass(frozen=True)
class Tool:
    column: str
    read: object  # (root, paths, spans) -> {(path, start): value}
    transforms: dict = field(default_factory=dict)  # ruling id -> (fn, context) -> delta
    set_aside: dict = field(default_factory=dict)  # ruling id -> (fn, context) -> bool


def rules(tools: dict) -> list:
    """Every transform and set-aside id the tools name, sorted."""
    return sorted(rule for tool in tools.values() for rule in (*tool.transforms, *tool.set_aside))


def expected(tool: Tool, fn, context, raw: int) -> int:
    return raw + sum(delta(fn, context) for delta in tool.transforms.values())


def set_aside(tool: Tool, fn, context) -> list:
    held = analysis_treesitter.reasons(fn, context, (tool.column,))
    return held + [name for name, holds in tool.set_aside.items() if holds(fn, context)]


def below(fn, kinds) -> list:
    """Nodes of `kinds` inside fn, at any depth."""
    found, stack = [], list(fn.children)
    while stack:
        node = stack.pop()
        found += [node] if node.type in kinds else []
        stack.extend(node.children)
    return found


def holds(kinds):
    """A set-aside test: fn has a node of `kinds` inside it."""
    return lambda fn, context: bool(below(fn, kinds))


@dataclass
class Outcome:
    compared: int = 0
    set_aside: Counter = field(default_factory=Counter)
    problems: list = field(default_factory=list)


def listed_except(kinds):
    """A language filter: every function tree-sitter lists but those of `kinds`."""
    def listed(path: str, data: bytes) -> list:
        return [(fn, context) for fn, _, context in analysis_treesitter.functions(path, data)
                if fn.type not in kinds]
    return listed


def spans(files: dict) -> dict:
    """{(path, start): end} of every function tree-sitter lists."""
    return {(path, counters.start_line(fn)): counters.end_line(fn)
            for path, data in files.items()
            for fn, _, _ in analysis_treesitter.functions(path, data)}


def _compare(outcome: Outcome, tool: Tool, where: tuple, fn, context, row, raw) -> None:
    outcome.compared += 1
    want = expected(tool, fn, context, raw)
    if row[tool.column] != want:
        outcome.problems.append((*where, tool.column, row[tool.column], want, raw))


def _judge(outcome: Outcome, tool: Tool, where: tuple, fn, context, row, raw) -> None:
    held = set_aside(tool, fn, context)
    if held:
        outcome.set_aside["+".join(held)] += 1
    elif None in (row, raw):
        outcome.problems.append((*where, "unjoined", row and row["long_name"], raw))
    else:
        _compare(outcome, tool, where, fn, context, row, raw)


def differential(name: str, tool: Tool, listed, sample: tuple) -> Outcome:
    """sample is (files, measured, root): the files, crapkit's run and where they are written."""
    files, measured, root = sample
    outcome = Outcome()
    answers = tool.read(root, sorted(files), spans(files))
    for path, data in files.items():
        rows = {row["start"]: row for row in measured.in_file(path)}
        for fn, context in listed(path, data):
            start = counters.start_line(fn)
            _judge(outcome, tool, (path, start), fn, context, rows.get(start),
                   answers.get((path, start)))
    runlog.note("skipped_files", oracle=f"{name}: functions set aside",
                count=sum(outcome.set_aside.values()))
    return outcome


def written(files: dict, root: Path) -> Path:
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    return root


def hand_values(tool: Tool, listed, sample: tuple, path: str, start: int) -> tuple:
    """(crapkit's value, the tool's raw value, fn, context) for the hand case at path:start."""
    files, measured, root = sample
    fn, context = next((fn, context) for fn, context in listed(path, files[path])
                       if counters.start_line(fn) == start)
    raw = tool.read(root, [path], spans({path: files[path]}))[(path, start)]
    crapkit = next(row for row in measured.in_file(path) if row["start"] == start)[tool.column]
    return crapkit, raw, fn, context
