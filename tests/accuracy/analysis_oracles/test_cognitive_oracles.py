"""Cognitive complexity against the Sonar paper and outside implementations of it.

Python has two outside readings:

- oracles/py_sonar.py, a counter written from G. Ann Campbell, "Cognitive
  Complexity" v1.7, run with crapkit's documented choices (analysis_choices);
- complexipy 8.0.1, whose own conventions are the counter's COMPLEXIPY choices
  (oracles/complexipy_adapter.py). complexipy checks the counter; the counter
  checks crapkit. Each convention where complexipy and crapkit part is a
  rulings row with a hand case holding complexipy's raw value and crapkit's.

The differentials read crapkit's own source on push and the standard library
nightly, on the ast.unparse form. A def holding a shape crapkit misreads is set
aside under its defect's rulings id (py_defect_shapes.COGNITIVE) and counted;
a def complexipy reads in a way the paper does not is set aside for complexipy.
"""
import ast

import pytest

from accuracy.analysis_oracles import (analysis_choices, analysis_js, analysis_pydiff,
                                       analysis_shapes, py_defect_shapes)
from accuracy.analysis_oracles.oracles import complexipy_adapter, py_sonar
from accuracy.kit import rulings, runlog

pytestmark = pytest.mark.process


def _counter(fn, source: str, path: str) -> int:
    return py_sonar.count(fn, analysis_choices.CRAPKIT).cognitive


def _sonar_differential(files: dict, measured, outcome=None) -> analysis_pydiff.Outcome:
    return analysis_pydiff.compare(files, measured, "cognitive", _counter,
                                   py_defect_shapes.COGNITIVE, outcome)


def _note(name: str, outcome: analysis_pydiff.Outcome) -> None:
    runlog.note("skipped_files", oracle=name, count=sum(outcome.set_aside.values()))


def test_python_cognitive_matches_the_sonar_counter(src_unparsed, src_unparsed_inventory,
                                                    py_shape_inventory):
    outcome = _sonar_differential(src_unparsed.files, src_unparsed_inventory)
    _sonar_differential(analysis_shapes.py_shape_files(), py_shape_inventory, outcome)
    _note("sonar counter: defs set aside", outcome)

    assert outcome.differing == []
    assert outcome.compared > 1500


@pytest.mark.nightly
def test_python_cognitive_matches_the_sonar_counter_on_the_stdlib(stdlib_unparsed,
                                                                  stdlib_unparsed_inventory):
    outcome = _sonar_differential(stdlib_unparsed.files, stdlib_unparsed_inventory)
    _note("sonar counter on the stdlib: defs set aside", outcome)

    assert outcome.differing == []


def test_every_departure_from_the_paper_is_a_definition_row():
    table = rulings.load()
    named = analysis_choices.RULINGS

    assert sorted(analysis_choices.departures()) == sorted(named)
    assert [rid for ids in named.values() for rid in ids
            if table[rid].ruling != "definition"] == []


# --- complexipy ----------------------------------------------------------------------------------

def _complexipy_reads(source: str) -> list[tuple]:
    """(def, complexipy's value) for each def complexipy reads the paper's way."""
    table = complexipy_adapter.values(source)
    comparable = filter(complexipy_adapter.comparable, (fn for fn, _ in analysis_pydiff.defs(source)))
    pairs = [(fn, complexipy_adapter.raw(fn, table)) for fn in comparable]
    return [pair for pair in pairs if pair[1] is not None]


def _complexipy_problems(files: dict) -> tuple[list, int]:
    """(defs where complexipy and the counter under COMPLEXIPY disagree, defs
    compared) over the defs complexipy reads the paper's way."""
    problems, compared = [], 0
    for path, data in files.items():
        read = _complexipy_reads(data.decode("utf-8") if isinstance(data, bytes) else data)
        compared += len(read)
        problems += [(path, fn.lineno, fn.name, raw) for fn, raw in read
                     if raw != complexipy_adapter.expected(fn)]
    return problems, compared


def test_complexipy_agrees_with_the_counter(src_unparsed, oracle):
    """complexipy is a second reading of the paper: where it reads a def the
    paper's way, the counter under complexipy's conventions gives its number."""
    oracle("complexipy")
    files = {**src_unparsed.files, **analysis_shapes.py_shape_files()}
    problems, compared = _complexipy_problems(files)

    assert problems == []
    assert compared > 1500


@pytest.mark.nightly
def test_complexipy_agrees_with_the_counter_on_the_stdlib(stdlib_unparsed, oracle):
    oracle("complexipy")
    problems, _ = _complexipy_problems(stdlib_unparsed.files)

    assert problems == []


# complexipy's raw value against crapkit's for one def each. The first eight
# are conventions (the counter reproduces complexipy under COMPLEXIPY); the
# last two are places complexipy reads nothing or another level, so a def
# holding them is set aside from the complexipy comparison.
COMPLEXIPY_CASES = {
    "AO-CXP-TERNARY": "def f(a, b):\n    return 1 if a else (2 if b else 3)\n",
    "AO-CXP-LAMBDA": "def f(a):\n    return lambda b: 1 if a else 2\n",
    "AO-CXP-ELEMENT": "def f(xs):\n    return [1 if x else 0 for x in xs]\n",
    "AO-CXP-ITER": "def f(a, b, c):\n    return [x for x in (a if b else c)]\n",
    "AO-CXP-CONDITION": "def f(xs):\n    if [x for x in xs]:\n        return 1\n    return 0\n",
    "AO-CXP-COMP-FLAT": "def f(x):\n    return [b for a in x for b in a]\n",
    "AO-CXP-COMP-FILTER": "def f(x):\n    return [a for a in x if a]\n",
    "AO-CXP-LOOP-ELSE": "def f(a):\n    for x in a:\n        a.pop()\n    else:\n        return 1\n",
    "AO-CXP-SKIPS": "def f(a, b):\n    return 1 + (a if b else 2)\n",
    "AO-CXP-NESTED-DEF": ("def f(a):\n    def g(b):\n        if b:\n            return 1\n"
                          "        return 0\n    return g(a)\n"),
}
OUTSIDE_THE_COMPARISON = {"AO-CXP-SKIPS", "AO-CXP-NESTED-DEF", "AO-CXP-LOOP-ELSE"}


@pytest.fixture(scope="module")
def complexipy_cases(measure_set):
    return measure_set({f"cases/{name}.py": source for name, source in COMPLEXIPY_CASES.items()})


@pytest.mark.parametrize("ruling_id", sorted(COMPLEXIPY_CASES))
def test_each_complexipy_convention_is_pinned_by_a_hand_case(ruling_id, complexipy_cases, oracle):
    oracle("complexipy")
    source = COMPLEXIPY_CASES[ruling_id]
    fn = ast.parse(source).body[0]
    raw = complexipy_adapter.raw(fn, complexipy_adapter.values(source))
    row = next(row for row in complexipy_cases.in_file(f"cases/{ruling_id}.py")
               if row["start"] == 1)

    assert complexipy_adapter.comparable(fn) == (ruling_id not in OUTSIDE_THE_COMPARISON)
    rulings.pin_ruling(ruling_id, crapkit=row["cognitive"], oracle=raw)


# --- JavaScript and TypeScript: sonarjs --------------------------------------------------------

def test_js_cognitive_matches_sonarjs(js_push, eslint_push):
    """eslint-plugin-sonarjs 4.2.1 cognitive-complexity against crapkit's column,
    over every function the compiler and crapkit both list."""
    pairs = analysis_js.all_pairs(js_push.measured, js_push.compiled)
    outcome = analysis_js.judge(analysis_js.Outcome(), pairs, analysis_js.ORACLES["cognitive"],
                                eslint_push("cognitive"))

    assert outcome.differing == []
    assert outcome.compared > 30


SONARJS_CASES = {"AO-SONARJS-RECURSION": ("ts/sonar.ts", 60, "cognitive"),
                 "AO-SONARJS-OR": ("js/callbacks.js", 6, "cognitive")}


@pytest.mark.parametrize("ruling_id", sorted(SONARJS_CASES))
def test_each_sonarjs_difference_is_pinned(ruling_id, js_push, eslint_push):
    crapkit, raw = analysis_js.case(js_push, eslint_push, *SONARJS_CASES[ruling_id])

    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)
