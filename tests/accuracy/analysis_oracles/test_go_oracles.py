"""Go ccn, cognitive and nesting against gocyclo, gocognit and revive.

The three tools live in the accuracy image (oracles/go_adapters.py), so this
runs on the Linux nightly cell, over the Go probe files and the full corpus's
cobra member. Each function tree-sitter lists is joined to crapkit's row and
the tool's answer by path and start line. A function a known crapkit defect
shape covers is set aside for that column (ts_defect_shapes), as in the
tree-sitter differential.

Where a tool counts a construct differently from crapkit's documented reading,
a named transform turns the tool's raw value into crapkit's, or the function
is set aside for that tool, and each such rule is a rulings row pinned below by
a hand case with the tool's raw value and crapkit's.
"""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from accuracy.analysis_oracles import (analysis_corpora, analysis_tables, analysis_treesitter,
                                       analysis_tstests)
from accuracy.analysis_oracles.oracles import go_adapters
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.kit import rulings, runlog

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]
LITERAL = "func_literal"


# --- the transforms and set-asides, each a rulings row ------------------------------------------

def _below(fn, kinds) -> list:
    """Nodes of `kinds` inside fn, at any depth."""
    found, stack = [], list(fn.children)
    while stack:
        node = stack.pop()
        found += [node] if node.type in kinds else []
        stack.extend(node.children)
    return found


def literal_decisions(fn, context) -> int:
    """AO-GOCYCLO-FUNC-LITERAL: gocyclo counts each func literal's decisions in the
    function that holds it; crapkit rows the literal on its own."""
    return sum(counters.ccn(lit, context.spec, context.data) - 1
               for lit in _below(fn, {LITERAL}))


def _plain_else(block) -> bool:
    parent = block.parent
    return (block.type == "block" and parent is not None and parent.type == "if_statement"
            and parent.child_by_field_name("alternative") == block)


def _else_if(node) -> bool:
    return node.parent is not None and node.parent.type == "if_statement" and \
        node.parent.child_by_field_name("alternative") == node


def _nesting_weighted(node, spec) -> bool:
    if node.type in spec.ifs:
        return not _else_if(node)
    return node.type in spec.loops | spec.switches


def _else_blocks_above(node, fn) -> int:
    count, parent = 0, node.parent
    while parent is not None and parent != fn:
        count += _plain_else(parent)
        parent = parent.parent
    return count


def else_nesting(fn, context) -> int:
    """AO-GOCOGNIT-ELSE-NESTING: gocognit walks a plain else block without raising
    the nesting level, so each nesting-weighted structure inside one scores one
    less per such else above it than the Sonar paper's B3 gives."""
    return sum(_else_blocks_above(node, fn) for node in counters.own_nodes(fn, context.spec)
               if _nesting_weighted(node, context.spec))


def _holds(kinds):
    return lambda fn, context: bool(_below(fn, kinds))


def _has_range(fn, context) -> bool:
    return any(kid.type == "range_clause" for loop in _below(fn, {"for_statement"})
               for kid in loop.children)


def _has_else(fn, context) -> bool:
    return any(_plain_else(node) or _else_if(node) for node in _below(fn, {"block",
                                                                            "if_statement"}))


@dataclass(frozen=True)
class Tool:
    column: str
    read: object  # (root, paths, spans) -> {(path, start): value}
    transforms: dict = field(default_factory=dict)  # ruling id -> (fn, context) -> delta
    set_aside: dict = field(default_factory=dict)  # ruling id -> (fn, context) -> bool


TOOLS = {
    "gocyclo": Tool("ccn_std", lambda root, paths, spans: go_adapters.gocyclo(root, paths),
                    transforms={"AO-GOCYCLO-FUNC-LITERAL": lambda fn, c: -literal_decisions(fn, c)}),
    "gocognit": Tool("cognitive", lambda root, paths, spans: go_adapters.gocognit(root, paths),
                     transforms={"AO-GOCOGNIT-ELSE-NESTING": else_nesting},
                     set_aside={"AO-GOCOGNIT-FUNC-LITERAL": _holds({LITERAL})}),
    "revive": Tool("nesting", go_adapters.revive_depths,
                   set_aside={"AO-REVIVE-ELSE": _has_else, "AO-REVIVE-RANGE": _has_range}),
}


def expected(tool: Tool, fn, context, raw: int) -> int:
    return raw + sum(delta(fn, context) for delta in tool.transforms.values())


def set_aside(tool: Tool, fn, context) -> list:
    held = analysis_treesitter.reasons(fn, context, (tool.column,))
    return held + [name for name, holds in tool.set_aside.items() if holds(fn, context)]


# --- the differential ----------------------------------------------------------------------------

@dataclass
class Outcome:
    compared: int = 0
    set_aside: Counter = field(default_factory=Counter)
    problems: list = field(default_factory=list)


def _declared(path: str, data: bytes) -> list:
    """(fn, context) for each named Go function: the tools list no func literal."""
    return [(fn, context) for fn, _, context in analysis_treesitter.functions(path, data)
            if fn.type != LITERAL]


def _spans(files: dict) -> dict:
    return {(path, counters.start_line(fn)): counters.end_line(fn)
            for path, data in files.items() for fn, _, _ in analysis_treesitter.functions(path, data)}


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


def differential(name: str, files: dict, measured, root: Path) -> Outcome:
    tool, outcome = TOOLS[name], Outcome()
    answers = tool.read(root, sorted(files), _spans(files))
    for path, data in files.items():
        rows = {row["start"]: row for row in measured.in_file(path)}
        for fn, context in _declared(path, data):
            start = counters.start_line(fn)
            _judge(outcome, tool, (path, start), fn, context, rows.get(start),
                   answers.get((path, start)))
    runlog.note("skipped_files", oracle=f"{name}: functions set aside",
                count=sum(outcome.set_aside.values()))
    return outcome


def _written(files: dict, root: Path) -> Path:
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    return root


@pytest.fixture(scope="module")
def go_probes(measure_set, tmp_path_factory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), "go")
    return files, measure_set(files), _written(files, tmp_path_factory.mktemp("go-probes"))


@pytest.fixture(scope="module")
def go_corpus(full_corpus, measure_set, tmp_path_factory):
    files = analysis_corpora.member_files(full_corpus, "cobra", (".go",))
    return files, measure_set(files), _written(files, tmp_path_factory.mktemp("go-corpus"))


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_go_probes_match_the_go_tools(name, go_probes, oracle):
    oracle(name)
    outcome = differential(name, *go_probes)

    assert outcome.problems == []
    assert outcome.compared > 5


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_cobra_matches_the_go_tools(name, go_corpus, oracle):
    oracle(name)
    outcome = differential(name, *go_corpus)

    assert outcome.problems == []
    assert outcome.compared > 50


# --- each rule's hand case: the tool's raw value and crapkit's ------------------------------------

LIT = ("package p\n\nfunc F(a []int) int {\n\tg := func(b int) int {\n\t\tif b > 0 {\n"
       "\t\t\treturn 1\n\t\t}\n\t\treturn 0\n\t}\n\tif len(a) > 0 {\n\t\treturn g(a[0])\n"
       "\t}\n\treturn 0\n}\n")
ELSE = ("package p\n\nfunc F(a bool, n int) int {\n\tif a {\n\t\treturn 1\n\t} else {\n"
        "\t\tfor i := 0; i < n; i++ {\n\t\t\tif i > 2 {\n\t\t\t\treturn i\n\t\t\t}\n\t\t}\n"
        "\t}\n\treturn 0\n}\n")
RANGE = ("package p\n\nfunc F(a []int) int {\n\tfor _, x := range a {\n\t\tif x > 0 {\n"
         "\t\t\treturn x\n\t\t}\n\t}\n\treturn 0\n}\n")
# ruling id -> (tool, source); crapkit's value and the tool's are read, the rule applied.
HAND = {
    "AO-GOCYCLO-FUNC-LITERAL": ("gocyclo", LIT),
    "AO-GOCOGNIT-ELSE-NESTING": ("gocognit", ELSE),
    "AO-GOCOGNIT-FUNC-LITERAL": ("gocognit", LIT),
    "AO-REVIVE-ELSE": ("revive", ELSE),
    "AO-REVIVE-RANGE": ("revive", RANGE),
}


@pytest.fixture(scope="module")
def hand(measure_set, tmp_path_factory):
    files = {f"cases/{ruling}.go": source.encode() for ruling, (_, source) in HAND.items()}
    return files, measure_set(files), _written(files, tmp_path_factory.mktemp("go-hand"))


@pytest.mark.parametrize("ruling_id", sorted(HAND))
def test_each_go_rule_is_pinned_by_a_hand_case(ruling_id, hand, oracle):
    name, _ = HAND[ruling_id]
    oracle(name)
    files, measured, root = hand
    path = f"cases/{ruling_id}.go"
    fn, context = _declared(path, files[path])[0]
    tool = TOOLS[name]
    raw = tool.read(root, [path], _spans({path: files[path]}))[(path, 3)]
    crapkit = next(row for row in measured.in_file(path) if row["start"] == 3)[tool.column]

    assert ruling_id in set_aside(tool, fn, context) or expected(tool, fn, context, raw) == crapkit
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


def test_every_go_rule_has_a_hand_case():
    names = {rule for tool in TOOLS.values() for rule in (*tool.transforms, *tool.set_aside)}

    assert sorted(names) == sorted(HAND)
