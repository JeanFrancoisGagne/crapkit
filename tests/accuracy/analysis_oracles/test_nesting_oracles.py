"""Nesting depth against a depth model and pylint.

crapkit documents a Python row's nesting (docs/agent-json.md "nesting") as the
deepest level of its cognitive pass: one level per if, elif, else, for, while,
except and comprehension for, none for with, try, finally, match, case or a
nested def. The model is oracles/py_sonar.py's depth, counted from the Sonar
paper's B2 nesting list with crapkit's documented choices (analysis_choices);
each place crapkit reads the paper another way is an N-row in rulings.tsv,
pinned by a hand probe. pylint 4.0.9's too-many-nested-blocks is the second
oracle (nightly): its block list differs from both in three named places, each
an N-row with a hand case, and a def holding one of them is left out of the
comparison and counted.
"""
import ast

import pytest

from accuracy.analysis_oracles import (analysis_choices, analysis_pydiff, analysis_shapes,
                                       py_defect_shapes)
from accuracy.analysis_oracles.oracles import py_sonar, pylint_nesting
from accuracy.kit import rulings, runlog

pytestmark = pytest.mark.process


def _depth(fn, source: str, path: str) -> int:
    return py_sonar.count(fn, analysis_choices.CRAPKIT).depth


def _model_differential(files: dict, measured, outcome=None) -> analysis_pydiff.Outcome:
    return analysis_pydiff.compare(files, measured, "nesting", _depth, py_defect_shapes.NESTING,
                                   outcome)


def test_python_depth_matches_model(src_unparsed, src_unparsed_inventory, py_shape_inventory):
    outcome = _model_differential(src_unparsed.files, src_unparsed_inventory)
    _model_differential(analysis_shapes.py_shape_files(), py_shape_inventory, outcome)
    runlog.note("skipped_files", oracle="depth model: defs set aside",
                count=sum(outcome.set_aside.values()))

    assert outcome.differing == []
    assert outcome.compared > 1500


@pytest.mark.nightly
def test_python_depth_matches_model_on_the_stdlib(stdlib_unparsed, stdlib_unparsed_inventory):
    outcome = _model_differential(stdlib_unparsed.files, stdlib_unparsed_inventory)

    assert outcome.differing == []


# --- pylint ------------------------------------------------------------------------------------

def _pylint_expected(reported: dict):
    def expected(fn, source: str, path: str):
        return pylint_nesting.depth_of(fn, path, reported) if pylint_nesting.comparable(fn) else None
    return expected


def _texts(files: dict) -> dict[str, str]:
    return {path: data.decode("utf-8") if isinstance(data, bytes) else data
            for path, data in files.items()}


@pytest.mark.nightly
def test_python_depth_matches_pylint(src_unparsed, src_unparsed_inventory, oracle, tmp_path):
    oracle("pylint")
    files = _texts(src_unparsed.files)
    reported = pylint_nesting.depths(files, tmp_path)
    outcome = analysis_pydiff.compare(files, src_unparsed_inventory, "nesting",
                                      _pylint_expected(reported), py_defect_shapes.NESTING)
    runlog.note("skipped_files", oracle="pylint R1702: defs set aside",
                count=sum(outcome.set_aside.values()))

    assert outcome.differing == []
    assert outcome.compared > 1000


# pylint's R1702 block list against crapkit's documented one, one def each:
# pylint opens a level for a try and reads an except body beside it; crapkit
# opens one for an except and one for a comprehension's for.
PYLINT_CASES = {
    "AO-N-PYLINT-TRY": ("def f(a):\n    try:\n        if a:\n            return 1\n"
                        "    finally:\n        a.close()\n"),
    "AO-N-PYLINT-EXCEPT": ("def f(a):\n    try:\n        a()\n    except E:\n        if a:\n"
                           "            return 2\n"),
    "AO-N-PYLINT-COMPREHENSION": "def f(a):\n    return [x for x in a if x]\n",
}


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
