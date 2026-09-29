"""The JavaScript and TypeScript differentials: crapkit's column against an
ESLint rule's number, function by function, over the functions the TypeScript
compiler lists.

Each oracle reads some constructs its own way (oracles/node_oracles.py tags
every function with the constructs it holds). ORACLES says, per oracle, which
column it checks, the transform that maps its number onto crapkit's documented
one (each a rulings row with a hand case), and which constructs set a function
aside because crapkit misreads them (a defect row) or the oracle does not read
them the paper's way. A set-aside function is counted, never compared. No
crapkit import.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from accuracy.analysis_oracles import analysis_shapes, analysis_tables
from accuracy.analysis_oracles.oracles import node_oracles

SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue")
# The probes of the shapes the corpus differential found crapkit misreading
# (test_js_corpus_oracles SHAPES). Each holds one misread on purpose, pinned by
# its hand probe and by that module's shape cases, so the push differentials
# leave these files out.
CORPUS_SHAPE_PROBES = "corpus_"


def push_files() -> dict:
    """The JS, TS and Vue probe files and the JS/TS shapes, less the corpus
    shape probes."""
    probes = {path: data for path, data in analysis_tables.probe_files().items()
              if path.endswith(SUFFIXES) and not is_corpus_shape_probe(path)}
    return {**probes, **analysis_shapes.ts_shape_files()}


def is_corpus_shape_probe(path: str) -> bool:
    return path.rpartition("/")[2].startswith(CORPUS_SHAPE_PROBES)


@dataclass(frozen=True)
class Setup:
    """One file set written out, listed by the compiler and measured by crapkit."""
    work: object
    paths: list
    compiled: list
    measured: object


def _defaults(fn, number: int) -> int:
    """AO-ESLINT-DEFAULTS: ESLint's complexity adds 1 per default parameter
    value; AO-ESLINT-PATTERN-DEFAULTS: and 1 per default value inside a
    destructuring pattern (its AssignmentPattern), where crapkit adds nothing
    for either."""
    return number - fn.defaults - fn.pattern_defaults


def _recursion(fn, number: int) -> int:
    """AO-SONARJS-RECURSION: sonarjs adds nothing for recursion; the paper adds 1."""
    return number + ("recursion" in fn.features)


def _same(fn, number: int) -> int:
    return number


@dataclass(frozen=True)
class Oracle:
    column: str
    transform: object
    set_aside: dict
    zero_when_silent: bool = False
    heads: bool = True
    only_without: frozenset = frozenset()


CCN_ASIDE = {"nullish": "D2a", "optional": "AO-TS-OPTIONAL-CHAIN"}
ORACLES = {
    "complexity-classic": Oracle("ccn_std", _defaults, CCN_ASIDE),
    "complexity-modified": Oracle("ccn_mod", _defaults, CCN_ASIDE),
    "cognitive": Oracle("cognitive", _recursion,
                        {"nullish": "D2b", "negated_logical": "AO-TS-COG-RUNS-NOT",
                         "nested_ternary": "AO-JS-COG-TERNARY", "or": "AO-SONARJS-OR"},
                        zero_when_silent=True),
    # crapkit keeps lizard's ND column for these languages (docs/agent-json.md
    # "nesting"); ESLint's max-depth counts nested blocks. They agree on if,
    # loop and block nesting only: a function holding any other construct is
    # compared by its N-row's hand case instead.
    "max-depth": Oracle("nesting", _same, {"nested_loop": "AO-ND-LOOPS"}, zero_when_silent=True,
                        heads=False,
                        only_without=frozenset({"else", "switch", "ternary", "label", "try",
                                                "and", "or", "nullish", "negated_logical"})),
}


@dataclass
class Outcome:
    compared: int = 0
    set_aside: Counter = field(default_factory=Counter)
    differing: list = field(default_factory=list)


def aside(fn, oracle: Oracle) -> str | None:
    """The rulings ids that set fn aside from the oracle, joined by +, or None."""
    hits = sorted(oracle.set_aside[name] for name in fn.features if name in oracle.set_aside)
    if hits:
        return "+".join(hits)
    return "outside the shared constructs" if fn.features & oracle.only_without else None


def expected(fn, oracle: Oracle, numbers: dict) -> int | None:
    """The oracle's number for fn after its transform; None when it reports none."""
    raw = numbers.get(fn, 0 if oracle.zero_when_silent else None)
    return None if raw is None else oracle.transform(fn, raw)


def judge(outcome: Outcome, pairs: list, oracle: Oracle, numbers: dict) -> Outcome:
    """Compare every (crapkit row, compiler function) pair under one oracle."""
    for row, fn in pairs:
        reason = aside(fn, oracle)
        if reason:
            outcome.set_aside[reason] += 1
            continue
        _one(outcome, row, fn, oracle, numbers)
    return outcome


def _one(outcome: Outcome, row: dict, fn, oracle: Oracle, numbers: dict) -> None:
    want = expected(fn, oracle, numbers)
    outcome.compared += 1
    if row[oracle.column] != want:
        outcome.differing.append((fn.path, fn.start, fn.name, row[oracle.column], want))


def rows_on(rows: list[dict], line: int) -> list[dict]:
    """crapkit's rows starting on `line`, in creation order."""
    return sorted((row for row in rows if row["start"] == line), key=lambda row: row["occurrence"])


def fns_on(fns: list, line: int) -> list:
    """The compiler's functions starting on `line`, left to right."""
    return sorted((fn for fn in fns if fn.start == line), key=lambda fn: fn.column)


def _on(line: int, rows: list[dict], fns: list) -> list[tuple]:
    """The rows in creation order zipped with the functions left to right on
    one line, when there are as many of each; else nothing."""
    ours, theirs = rows_on(rows, line), fns_on(fns, line)
    return list(zip(ours, theirs)) if len(ours) == len(theirs) else []


def pairs(rows: list[dict], fns: list) -> list[tuple]:
    """(crapkit row, compiler function) in order, on each line where both list
    the same number of functions."""
    lines = sorted({row["start"] for row in rows} & {fn.start for fn in fns})
    return [pair for line in lines for pair in _on(line, rows, fns)]


def in_file(fns: list, path: str) -> list:
    return [fn for fn in fns if fn.path == path]


def all_pairs(measured, fns: list) -> list[tuple]:
    paths = sorted({fn.path for fn in fns})
    return [pair for path in paths for pair in pairs(measured.in_file(path), in_file(fns, path))]


def case(setup: Setup, eslint, path: str, start: int, mode: str) -> tuple:
    """(crapkit's value, the oracle's number) for the function starting on a
    hand case's line."""
    oracle = ORACLES[mode]
    (fn,) = fns_on(in_file(setup.compiled, path), start)
    (row,) = rows_on(setup.measured.in_file(path), start)
    return row[oracle.column], expected(fn, Oracle(oracle.column, _same, {},
                                                    oracle.zero_when_silent), eslint(mode))


def numbers(fns: list, found: dict, mode: str) -> dict:
    return node_oracles.per_function(fns, found, ORACLES[mode].heads)
