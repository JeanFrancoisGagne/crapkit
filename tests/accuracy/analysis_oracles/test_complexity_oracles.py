"""ccn_std, ccn_mod and the gated ccn against outside tools.

Python: radon 6.0.1 and mccabe 0.7.0 count McCabe's decisions off the ast
(oracles/radon_mccabe.py). Each place a tool counts differently from crapkit's
documented reading is a named transform and a rulings row, pinned here by one
hand case with the tool's raw value and crapkit's. The differential runs over
crapkit's own source on push and CPython's Lib at the running Python's pinned
tag nightly, on the ast.unparse form (a layout difference is test_metamorphic_source's
business); a def holding a shape crapkit misreads is set aside under its
rulings id and counted.

The gate reads ccn = min(ccn_std, ccn_mod) (docs/agent-json.md "ccn"), which
is checked as a property on every row crapkit wrote.
"""
import ast

import pytest

from accuracy.analysis_oracles import (analysis_js, analysis_pydiff, analysis_shapes,
                                       analysis_tables, py_defect_shapes)
from accuracy.analysis_oracles.oracles import radon_mccabe
from accuracy.analysis_oracles import analysis_tables, analysis_tstests
from accuracy.kit import rulings, runlog

pytestmark = pytest.mark.process


def analysis_tables_marks(ruling_id: str):
    """The strict-xfail mark of an open defect row, applied as a decorator."""
    def apply(test):
        for mark in analysis_tables.marks(ruling_id):
            test = mark(test)
        return test
    return apply


def _radon(fn, source: str, path: str):
    raw = radon_mccabe.radon_values(source).get((fn.lineno, fn.col_offset))
    return None if raw is None else radon_mccabe.radon_expected(fn, raw)


def _mccabe(fn, source: str, path: str):
    raw = radon_mccabe.mccabe_values(source).get((fn.lineno, fn.col_offset))
    return None if raw is None else radon_mccabe.mccabe_expected(fn, raw, source)


TOOLS = {"radon": _radon, "mccabe": _mccabe}


def _differential(tool: str, corpus, measured, oracle) -> analysis_pydiff.Outcome:
    oracle(tool)
    outcome = analysis_pydiff.compare(corpus.files, measured, "ccn_std", TOOLS[tool],
                                      py_defect_shapes.CCN)
    runlog.note("skipped_files", oracle=f"{tool}: defs set aside",
                count=sum(outcome.set_aside.values()), compared=outcome.compared,
                misses=len(outcome.differing))
    return outcome


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_python_ccn_matches_radon_and_mccabe(tool, src_unparsed, src_unparsed_inventory, oracle):
    outcome = _differential(tool, src_unparsed, src_unparsed_inventory, oracle)

    assert outcome.differing == []
    assert outcome.compared > 1500


@pytest.mark.nightly
@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_python_ccn_matches_radon_and_mccabe_on_the_stdlib(tool, stdlib_unparsed,
                                                           stdlib_unparsed_inventory, oracle):
    outcome = _differential(tool, stdlib_unparsed, stdlib_unparsed_inventory, oracle)

    assert outcome.differing == []


# Each transform's hand case: the tool's raw value, the value after every
# transform, which is crapkit's, worked by hand from NIST SP 500-235 sec. 4.1
# and lizard's Python reader (if, elif, for, while, except, finally, and, or,
# a conditional expression, each case arm and guard).
TRANSFORM_CASES = {
    "AO-RADON-ASSERT": "def f(a):\n    assert a\n    return a\n",
    "AO-RADON-FINALLY": ("def f(a):\n    try:\n        a()\n    except E:\n        return 1\n"
                         "    finally:\n        a.close()\n"),
    "AO-RADON-ELSE": "def f(a):\n    for x in a:\n        a.pop()\n    else:\n        return 1\n",
    "AO-RADON-GUARD": ("def f(a, b):\n    match a:\n        case 1 if b:\n            return 1\n"
                       "        case 2:\n            return 2\n"),
    "AO-RADON-CLASS-BODY": "def f(a):\n    class K:\n        x = 1 if a else 2\n    return K\n",
    "AO-MCCABE-TRY": ("def f(a):\n    try:\n        a()\n    except E:\n        return 1\n"
                      "    return 0\n"),
    "AO-MCCABE-EXPRESSIONS": "def f(a, b):\n    return a and b\n",
    "AO-MCCABE-SKIPPED": ("def f(a, b):\n    match a:\n        case 1 if b:\n            return 1\n"
                          "        case 2:\n            return 2\n"),
    "AO-MCCABE-NESTED": ("def f(a):\n    def g(b):\n        if b:\n            return 1\n"
                         "        return 0\n    return g(a)\n"),
}


def _raw(ruling_id: str, source: str) -> int:
    values = (radon_mccabe.radon_values if ruling_id.startswith("AO-RADON")
              else radon_mccabe.mccabe_values)
    return values(source)[(1, 0)]


def _transformed(ruling_id: str, source: str, raw: int) -> int:
    fn = ast.parse(source).body[0]
    if ruling_id.startswith("AO-RADON"):
        return radon_mccabe.radon_expected(fn, raw)
    return radon_mccabe.mccabe_expected(fn, raw, source)


@pytest.fixture(scope="module")
def transform_cases(measure_set):
    return measure_set({f"cases/{name}.py": source for name, source in TRANSFORM_CASES.items()})


@pytest.mark.parametrize("ruling_id", sorted(TRANSFORM_CASES))
def test_each_transform_is_pinned_by_a_hand_case(ruling_id, transform_cases):
    source = TRANSFORM_CASES[ruling_id]
    raw = _raw(ruling_id, source)
    row = next(row for row in transform_cases.in_file(f"cases/{ruling_id}.py")
               if row["start"] == 1)

    assert _transformed(ruling_id, source, raw) == row["ccn_std"]
    rulings.pin_ruling(ruling_id, crapkit=row["ccn_std"], oracle=raw)


def test_every_transform_has_a_hand_case():
    names = {*radon_mccabe.RADON, *radon_mccabe.MCCABE}

    assert sorted(names) == sorted(TRANSFORM_CASES)


# --- the gated ccn -----------------------------------------------------------------------------

def _gate_problems(rows) -> list:
    return [row for row in rows
            if not (row["ccn"] == min(row["ccn_std"], row["ccn_mod"]) and row["ccn"] >= 1)]


def test_ccn_is_the_smaller_of_std_and_mod_on_every_row(src_inventory, probe_inventory):
    """docs/agent-json.md "ccn": min(ccn_std, ccn_mod), and a function is at least
    one path."""
    assert _gate_problems(src_inventory.rows) == []
    assert _gate_problems(probe_inventory.rows) == []


# The languages whose rows break the rule below, each under its rulings row.
MOD_OVER_STD = {".ps1": "AO-PS-MOD-OVER-STD", ".psm1": "AO-PS-MOD-OVER-STD",
                ".zig": "AO-ZIG-MOD-OVER-STD"}


def _mod_over_std_ruling(path: str) -> str:
    return MOD_OVER_STD.get("." + path.rpartition(".")[2].lower(), "")


@pytest.mark.parametrize("ruling_id", [
    pytest.param(ruling_id, id=ruling_id or "every other language",
                 marks=analysis_tables.marks(ruling_id))
    for ruling_id in sorted({"", *MOD_OVER_STD.values()})])
def test_ccn_mod_never_exceeds_ccn_std(ruling_id, src_inventory, probe_inventory):
    """lizard -m only ever merges a switch's cases into one decision, so a row's
    ccn_mod is at most its ccn_std."""
    over = [row for row in [*src_inventory.rows, *probe_inventory.rows]
            if row["ccn_mod"] > row["ccn_std"] and _mod_over_std_ruling(row["path"]) == ruling_id]

    analysis_tables.check(str(len(over)), "0", ruling_id)


def _modified(fn, source: str, path: str) -> int:
    return radon_mccabe.modified(fn)


def test_python_ccn_mod_counts_each_match_as_one_decision(src_unparsed, src_unparsed_inventory,
                                                          py_shape_inventory):
    """ccn_mod is ccn_std with each switch read as one decision (lizard README,
    option -m); Python's match is that switch."""
    shapes = analysis_shapes.py_shape_files()
    detectors = {"AO-PY-FLOORDIV": py_defect_shapes.floor_division}
    outcome = analysis_pydiff.compare(src_unparsed.files, src_unparsed_inventory, "ccn_mod",
                                      _modified, detectors)
    analysis_pydiff.compare(shapes, py_shape_inventory, "ccn_mod", _modified, detectors, outcome)

    assert outcome.differing == []


# --- JavaScript and TypeScript: ESLint's complexity rule -----------------------------------------

ESLINT_CCN = ("complexity-classic", "complexity-modified")


@pytest.mark.parametrize("mode", ESLINT_CCN)
def test_js_ccn_matches_eslint(mode, js_push, eslint_push):
    """ESLint 10.11.0 complexity, variant classic against ccn_std and modified
    against ccn_mod, over every function the compiler and crapkit both list."""
    pairs = analysis_js.all_pairs(js_push.measured, js_push.compiled)
    outcome = analysis_js.judge(analysis_js.Outcome(), pairs, analysis_js.ORACLES[mode],
                                eslint_push(mode))

    assert outcome.differing == []
    assert outcome.compared > 40


JS_CASES = {"AO-ESLINT-DEFAULTS": ("ts/shapes.ts", 1, "complexity-classic")}


@pytest.mark.parametrize("ruling_id", sorted(JS_CASES))
def test_each_eslint_complexity_transform_is_pinned(ruling_id, js_push, eslint_push):
    crapkit, raw = analysis_js.case(js_push, eslint_push, *JS_CASES[ruling_id])

    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


# --- brace languages and shell: the tree-sitter counters (function spans) -------------------------

@pytest.mark.parametrize("language", sorted(analysis_tstests.LANGUAGES))
def test_spans_match_the_treesitter_counters_on_the_probes(language, probe_inventory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), language)

    outcome = analysis_tstests.check(files, probe_inventory, "spans", language)
    assert outcome.compared > 0


@pytest.mark.nightly
@pytest.mark.parametrize("language", analysis_tstests.CORPUS_LANGUAGES)
def test_spans_match_the_treesitter_counters_on_the_corpus(language, corpus_language):
    files, measured = corpus_language(language)

    outcome = analysis_tstests.check(files, measured, "spans", language)
    assert outcome.compared > 0


# --- brace languages and shell: the tree-sitter counters (ccn_std and ccn_mod) -------------------------

@pytest.mark.parametrize("language", sorted(analysis_tstests.LANGUAGES))
def test_ccn_match_the_treesitter_counters_on_the_probes(language, probe_inventory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), language)

    outcome = analysis_tstests.check(files, probe_inventory, "ccn", language)
    assert outcome.compared > 0


@pytest.mark.nightly
@pytest.mark.parametrize("language", analysis_tstests.CORPUS_LANGUAGES)
def test_ccn_match_the_treesitter_counters_on_the_corpus(language, corpus_language):
    files, measured = corpus_language(language)

    outcome = analysis_tstests.check(files, measured, "ccn", language)
    assert outcome.compared > 0
