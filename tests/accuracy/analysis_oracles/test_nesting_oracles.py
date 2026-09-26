"""Nesting depth against a depth model and pylint.

crapkit documents a Python row's nesting (docs/agent-json.md "nesting") as the
deepest level of its cognitive pass: one level per if, elif, else, for, while,
except and comprehension for, none for with, try, finally, match, case or a
nested def. The model is oracles/py_sonar.py's depth, counted from the Sonar
paper's B2 nesting list with crapkit's documented choices (analysis_choices);
each place crapkit reads the paper another way is an N-row in rulings.tsv,
pinned by a hand probe. pylint 4.0.9's too-many-nested-blocks is the second
oracle (nightly, on crapkit's source and on CPython's Lib at the running
Python's pinned tag): its block list differs from both in three named places, each
an N-row with a hand case, and a def holding one of them is left out of the
comparison and counted.
"""
import ast

import pytest

from accuracy.analysis_oracles import (analysis_choices, analysis_js, analysis_pydiff,
                                       analysis_shapes, py_defect_shapes)
from accuracy.analysis_oracles.oracles import py_sonar, pylint_nesting
from accuracy.analysis_oracles import analysis_tables, analysis_tstests
from accuracy.kit import rulings, runlog

pytestmark = pytest.mark.process


def _depth(fn, source: str, path: str) -> int:
    return py_sonar.count(fn, analysis_choices.CRAPKIT).depth


def _model_differential(files: dict, measured, outcome=None) -> analysis_pydiff.Outcome:
    return analysis_pydiff.compare(files, measured, "nesting", _depth, py_defect_shapes.NESTING,
                                   outcome)


def _note(name: str, outcome: analysis_pydiff.Outcome) -> None:
    runlog.note("skipped_files", oracle=name, count=sum(outcome.set_aside.values()),
                compared=outcome.compared, misses=len(outcome.differing))


def test_python_depth_matches_model(src_unparsed, src_unparsed_inventory, py_shape_inventory):
    outcome = _model_differential(src_unparsed.files, src_unparsed_inventory)
    _model_differential(analysis_shapes.py_shape_files(), py_shape_inventory, outcome)
    _note("depth model: defs set aside", outcome)

    assert outcome.differing == []
    assert outcome.compared > 1500


@pytest.mark.nightly
def test_python_depth_matches_model_on_the_stdlib(stdlib_unparsed, stdlib_unparsed_inventory):
    outcome = _model_differential(stdlib_unparsed.files, stdlib_unparsed_inventory)
    _note("depth model on the stdlib: defs set aside", outcome)

    assert outcome.differing == []


# --- pylint ------------------------------------------------------------------------------------

def _pylint_expected(reported: dict):
    def expected(fn, source: str, path: str):
        return pylint_nesting.depth_of(fn, path, reported) if pylint_nesting.comparable(fn) else None
    return expected


def _texts(files: dict) -> dict[str, str]:
    return {path: data.decode("utf-8") if isinstance(data, bytes) else data
            for path, data in files.items()}


def _pylint_differential(corpus, measured, work) -> analysis_pydiff.Outcome:
    files = _texts(corpus.files)
    reported = pylint_nesting.depths(files, work)
    return analysis_pydiff.compare(files, measured, "nesting", _pylint_expected(reported),
                                   py_defect_shapes.NESTING)


@pytest.mark.nightly
def test_python_depth_matches_pylint(src_unparsed, src_unparsed_inventory, oracle, tmp_path):
    oracle("pylint")
    outcome = _pylint_differential(src_unparsed, src_unparsed_inventory, tmp_path)
    _note("pylint R1702: defs set aside", outcome)

    assert outcome.differing == []
    assert outcome.compared > 1000


@pytest.mark.nightly
def test_python_depth_matches_pylint_on_the_stdlib(stdlib_unparsed, stdlib_unparsed_inventory,
                                                   oracle, tmp_path):
    """pylint over CPython's Lib at the running Python's pinned tag, the second
    oracle beside the depth model on the same defs."""
    oracle("pylint")
    outcome = _pylint_differential(stdlib_unparsed, stdlib_unparsed_inventory, tmp_path)
    _note("pylint R1702 on the stdlib: defs set aside", outcome)

    assert outcome.differing == []
    assert outcome.compared > 1000


# pylint's R1702 block list against crapkit's documented one, one def each:
# pylint opens a level for a try and reads an except body beside it; crapkit
# opens one for an except and one for a comprehension's for. The last two are
# pylint bugs (oracles/pylint_nesting.py): an async def's only if reads 0, and
# an if right under a with inside an if restarts at 1.
PYLINT_CASES = {
    "AO-N-PYLINT-TRY": ("def f(a):\n    try:\n        if a:\n            return 1\n"
                        "    finally:\n        a.close()\n"),
    "AO-N-PYLINT-EXCEPT": ("def f(a):\n    try:\n        a()\n    except E:\n        if a:\n"
                           "            return 2\n"),
    "AO-N-PYLINT-COMPREHENSION": "def f(a):\n    return [x for x in a if x]\n",
    "AO-N-PYLINT-ASYNC": "async def f(a):\n    if a:\n        return 1\n    return 0\n",
    "AO-N-PYLINT-WITH": ("def f(a, b):\n    if a:\n        with b:\n            if b:\n"
                         "                return 1\n    return 0\n"),
}


# Which with shapes AO-N-PYLINT-WITH sets aside. pylint 4.0.9 read each by hand:
# a with at the top holding if/if 2, an if after a with holding no block 2 (crapkit
# 2 for both); a while in a with in a with in a for 1, where crapkit reads 2.
WITH_SHAPES = {
    "with at the top": ("def f(a, b):\n    with b:\n        if a:\n            if b:\n"
                        "                return 1\n", True),
    "with holding no block": ("def f(a, b):\n    if a:\n        with b:\n            b()\n"
                              "        if b:\n            return 1\n", True),
    "with in a with in a for": ("def f(a, b):\n    for x in a:\n        with b:\n"
                                "            with x:\n                while x:\n"
                                "                    x()\n", False),
}


@pytest.mark.parametrize("name", sorted(WITH_SHAPES))
def test_only_a_block_under_a_nested_with_is_set_aside_for_pylint(name):
    source, comparable = WITH_SHAPES[name]

    assert pylint_nesting.comparable(ast.parse(source).body[0]) is comparable


@pytest.fixture(scope="module")
def pylint_cases(measure_set):
    return measure_set({f"cases/{name}.py": source for name, source in PYLINT_CASES.items()})


@pytest.mark.nightly
@pytest.mark.parametrize("ruling_id", sorted(PYLINT_CASES))
def test_each_pylint_difference_is_pinned_by_a_hand_case(ruling_id, pylint_cases, oracle, tmp_path):
    oracle("pylint")
    path = f"cases/{ruling_id}.py"
    reported = pylint_nesting.depths({path: PYLINT_CASES[ruling_id]}, tmp_path)
    fn = ast.parse(PYLINT_CASES[ruling_id]).body[0]
    (row,) = pylint_cases.in_file(path)

    assert not pylint_nesting.comparable(fn)
    rulings.pin_ruling(ruling_id, crapkit=row["nesting"],
                       oracle=pylint_nesting.depth_of(fn, path, reported))


# --- JavaScript and TypeScript: ESLint's max-depth ------------------------------------------------

def test_js_depth_matches_eslint_max_depth(js_push, eslint_push):
    """ESLint 10.11.0 max-depth against crapkit's nesting (lizard's ND column,
    docs/agent-json.md "nesting") on every function built of if, loop and block
    nesting only, where the two definitions coincide."""
    pairs = analysis_js.all_pairs(js_push.measured, js_push.compiled)
    outcome = analysis_js.judge(analysis_js.Outcome(), pairs, analysis_js.ORACLES["max-depth"],
                                eslint_push("max-depth"))

    assert outcome.differing == []
    assert outcome.compared > 20


# Where lizard's ND and ESLint's max-depth part, one function each.
DEPTH_CASES = {"AO-N-ESLINT-ELSE": ("ts/flow.ts", 17), "AO-N-ESLINT-SWITCH": ("ts/flow.ts", 94),
               "AO-N-ESLINT-TERNARY": ("ts/flow.ts", 75), "AO-N-ESLINT-LABEL": ("ts/sonar.ts", 4),
               "AO-N-ESLINT-TRY": ("ts/sonar.ts", 30), "AO-N-ESLINT-LOGICAL": ("ts/sonar.ts", 46)}


@pytest.mark.parametrize("ruling_id", sorted(DEPTH_CASES))
def test_each_max_depth_difference_is_pinned(ruling_id, js_push, eslint_push):
    crapkit, raw = analysis_js.case(js_push, eslint_push, *DEPTH_CASES[ruling_id], "max-depth")

    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


# --- brace languages and shell: the tree-sitter counters (nesting depth) -------------------------

@pytest.mark.parametrize("language", sorted(analysis_tstests.LANGUAGES))
def test_nesting_match_the_treesitter_counters_on_the_probes(language, probe_inventory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), language)

    outcome = analysis_tstests.check(files, probe_inventory, "nesting", language)
    assert outcome.compared > 0


@pytest.mark.nightly
@pytest.mark.parametrize("language", analysis_tstests.CORPUS_LANGUAGES)
def test_nesting_match_the_treesitter_counters_on_the_corpus(language, corpus_language):
    files, measured = corpus_language(language)

    outcome = analysis_tstests.check(files, measured, "nesting", language)
    assert outcome.compared > 0
