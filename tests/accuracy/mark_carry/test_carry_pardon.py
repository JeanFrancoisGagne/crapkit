"""Gate pardon over a carried mark: `gate.judge` asks `keys.resolve` which mark
judges each function over its ceiling, and a carried mark judges by the usual rule.

Three checks, each with its expected value worked without crapkit:

1. hand_pardon.tsv, a file's functions, its marks, the asked function's ccn
   and CRAP, and the answer (pass, refuse or ambiguous), worked by hand from
   docs/ratchet.md. The gate gives each row's answer.
2. carry_oracle.py pairs marks to functions by the same words over the long
   names lizard prints, and agrees with every hand row.
3. A property: the gate's verdict on a carried function equals its verdict
   with the same mark written under the function's own key.

The probe row fails against 0.8.1's rule, a mark judging only its exact key,
which is the retro check. crapkit is loaded by name when a test runs
(kit/closure.py follows import statements only).
"""
from __future__ import annotations

import importlib
from pathlib import Path

from hypothesis import given, strategies as st
import pytest

from accuracy.kit.settings import pure
from accuracy.mark_carry import carry_oracle as oracle

HERE = Path(__file__).resolve().parent
PATH = "calc/grade.py"
CEILING = 6
OLD, SIG = "classify( a , b )", "classify( a , b , c = None )"


def gate():
    return importlib.import_module("crapkit.gate")


def hand_rows() -> list[dict]:
    lines = (HERE / "hand_pardon.tsv").read_bytes().decode("utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


HAND = hand_rows()


def names_of(row: dict) -> list[str]:
    return row["functions"].split(" | ")


def marks_of(row: dict) -> dict[str, float]:
    pairs = (cell.rsplit("=", 1) for cell in row["marks"].split(" | "))
    return {name: float(value) for name, value in pairs}


def gate_answer(names: list[str], marks: dict[str, float], asked: str, crap: float) -> str:
    """The gate's verdict on ASKED at CRAP, every other function of the file
    under its ceiling: pass, refuse, or ambiguous when a refused carry names why."""
    module, keys = gate(), oracle.keyed(names)
    functions = tuple(module.Function(name, 1 + 10 * n, 6 + 10 * n,
                                      module.CrapBound(crap, crap) if keys[n] == asked
                                      else module.CrapBound(1.0, 1.0))
                      for n, name in enumerate(names))
    index = importlib.import_module("crapkit.keys").MarkIndex(
        importlib.import_module("crapkit.ratchet").RatchetEntry(PATH, key, value)
        for key, value in marks.items())
    found = module.judge([module.ChangedFile(PATH, module.WHOLE, functions)], lambda scope: CEILING,
                         lambda: index)
    if found.pardoned or found.carried:
        return "pass"
    refused = [b for b in found.over_ceiling if b.mark_ambiguity]
    return "ambiguous" if refused else "refuse"


def _row_args(row: dict) -> tuple:
    return names_of(row), marks_of(row), row["asked"], float(row["crap"])


# --- check 1: the hand table ---------------------------------------------------------------------

@pytest.mark.parametrize("row", HAND, ids=lambda r: r["case"])
def test_every_hand_row_gets_its_answer_from_the_gate(row):
    assert gate_answer(*_row_args(row)) == row["answer"]


def test_the_hand_table_holds_every_answer_and_ccn_is_crap_in_a_cc_only_scope():
    assert {row["answer"] for row in HAND} == {"pass", "refuse", "ambiguous"}
    assert all(int(row["ccn"]) == round(float(row["crap"])) for row in HAND)


# --- check 2: the oracle over lizard's names -----------------------------------------------------

@pytest.mark.parametrize("row", HAND, ids=lambda r: r["case"])
def test_the_oracle_agrees_with_every_hand_row(row):
    names, marks, asked, crap = _row_args(row)

    assert oracle.answer(oracle.keyed(names), marks, asked, crap) == row["answer"]


def test_lizard_names_the_probe_s_two_signatures_as_the_hand_table_does():
    body = "".join(f"    if a == {n}:\n        return {n}\n" for n in range(6)) + "    return b\n"

    before = oracle.long_names("def classify(a, b):\n" + body)
    after = oracle.long_names("def classify(a, b, c=None):\n" + body)

    assert (before, after) == ([OLD], [SIG])
    assert oracle.answer(oracle.keyed(after), {before[0]: 7.0}, after[0], 7.0) == "pass"


def test_the_probe_row_fails_against_the_exact_key_rule():
    """Retro detection: 0.8.1 read a mark under its exact key only."""
    (probe,) = [row for row in HAND if row["case"].startswith("the probe:")]
    _, marks, asked, crap = _row_args(probe)

    assert (oracle.exact_key_answer(marks, asked, crap), probe["answer"]) == ("refuse", "pass")


# --- check 3: a carry moves the key, never the verdict -------------------------------------------

CRAP = st.floats(min_value=6.5, max_value=80.0, allow_nan=False).map(lambda value: round(value, 5))
MARK = st.integers(65_000, 800_000).map(lambda value: value / 10_000)


@given(crap=CRAP, mark=MARK)
@pure
def test_a_carried_verdict_equals_the_verdict_under_the_function_s_own_key(crap, mark):
    carried = gate_answer([SIG], {OLD: mark}, SIG, crap)
    own = gate_answer([SIG], {SIG: mark}, SIG, crap)

    assert carried == own == ("pass" if round(crap, 4) <= mark else "refuse")
