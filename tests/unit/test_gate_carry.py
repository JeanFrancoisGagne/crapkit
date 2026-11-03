"""gate.judge pardons through a carried mark, by keys.resolve's answer.

Every keys row mission-4-01 wrote (test_keys_resolve's HAND and REFUSED) runs
through `gate.judge` here: the function the row asks about sits over its
ceiling, every other function of its file under it, and each marked key holds
a mark the breach sits under. A Carry row pardons and yields one `carried`
finding; a Refused row pardons nothing and carries keys' sentence as
`mark_ambiguity`; every other row pardons exactly as mission-3 left it.
"""
from __future__ import annotations

import itertools

import pytest

from crapkit import gate, keys
from crapkit.gate import WHOLE, ChangedFile, CrapBound, Function
from crapkit.keys import Carry, MarkIndex, split_ordinal
from crapkit.ratchet import RatchetEntry
from marks_history_repo import NEW, OLD, PATH, TWIN
from test_keys_resolve import HAND, REFUSED

CEILING = 6
MARK = 50.0


class Marks:
    """The marks callable over {key name: mark} in PATH, counting its calls."""

    def __init__(self, marks: dict[str, float]):
        self.marks, self.calls = marks, 0

    def __call__(self) -> MarkIndex:
        self.calls += 1
        return MarkIndex(RatchetEntry(path, name, mark) for (path, name), mark in self.marks.items())


def functions_of(names, over: str, bound: CrapBound) -> tuple[Function, ...]:
    """One Function per key name, twins of one name placed in ordinal order, so
    keys.key_names hands back the same key names; `over` takes `bound`."""
    counts: dict[str, int] = {}
    for name in names:
        base, ordinal = split_ordinal(name)
        counts[base] = max(counts.get(base, 0), ordinal)
    found, line = [], 1
    for base in sorted(counts):
        for ordinal in range(1, counts[base] + 1):
            key = keys.key_name(base, ordinal)
            found.append(Function(base, line, line + 5, bound if key == over else CrapBound(1.0, 1.0)))
            line += 10
    return tuple(found)


def judged(function: str, functions, marked: dict, bound=CrapBound(10.0, 10.0)):
    """The gate's result when FUNCTION, one of FUNCTIONS, sits at BOUND and
    MARKED holds the marks, with the marks callable it was handed."""
    fetch = Marks(marked)
    names = {name for _, name in functions} | {function}
    change = ChangedFile(PATH, WHOLE, functions_of(names, function, bound))
    return gate.judge([change], lambda scope: CEILING, fetch), fetch


def marks_at(marked, value: float = MARK) -> dict:
    return {key: value for key in marked}


@pytest.mark.parametrize("case, function, functions, marked, answer", HAND, ids=[r[0] for r in HAND])
def test_every_keys_row_pardons_by_its_answer(case, function, functions, marked, answer):
    result, fetch = judged(function, functions, marks_at(marked))

    assert fetch.calls == 1
    if isinstance(answer, Carry):
        (carried,) = result.carried
        assert (carried.key_name, carried.carried_from, carried.mark) == (function, answer.old_key[1], MARK)
        assert (result.pardoned, result.over_ceiling) == ((), ())
    elif answer.key in marked:
        assert [(b.key_name, b.mark) for b in result.pardoned] == [(function, MARK)]
        assert result.carried == ()
    else:
        (breach,) = result.over_ceiling
        assert (breach.key_name, breach.mark, breach.mark_ambiguity) == (function, None, None)
        assert (result.carried, result.pardoned) == ((), ())


@pytest.mark.parametrize("case, function, functions, marked, named", REFUSED, ids=[r[0] for r in REFUSED])
def test_a_refused_row_pardons_nothing_and_carries_keys_sentence(case, function, functions, marked, named):
    result, _ = judged(function, functions, marks_at(marked))

    (breach,) = result.over_ceiling
    sentence = keys.resolve((PATH, function), functions | {(PATH, function)}, marked).text
    assert (breach.key_name, breach.mark, breach.mark_ambiguity) == (function, None, sentence)
    assert (result.carried, result.pardoned) == ((), ())


def test_the_signature_probe_is_carried_at_its_mark():
    """seed wrote classify( a , b ) at 7.0000; the edit gave it `c=None` at ccn 7."""
    result, fetch = judged(NEW, {(PATH, NEW)}, {(PATH, OLD): 7.0}, CrapBound(7.0, 7.0))

    (carried,) = result.carried
    assert (carried.path, carried.key_name, carried.carried_from, carried.mark) == (PATH, NEW, OLD, 7.0)
    assert carried.function.long_name == NEW
    assert (gate.GateResult(judged=1, carried=result.carried), fetch.calls) == (result, 1)


def test_a_second_edit_past_the_carried_mark_is_a_marked_rise_with_no_carry():
    result, _ = judged(NEW, {(PATH, NEW)}, {(PATH, OLD): 7.0}, CrapBound(8.0, 8.0))

    assert [(b.key_name, b.mark) for b in result.marked_rise] == [(NEW, 7.0)]
    assert result.carried == ()


def test_a_twin_left_by_the_edit_refuses_and_names_the_move():
    """`classify` and `classify#2` after the edit: twins never carry a mark."""
    result, _ = judged(TWIN, {(PATH, NEW), (PATH, TWIN)}, {(PATH, NEW): 7.0, (PATH, OLD): 7.0})

    (breach,) = result.over_ceiling
    assert "twins never carry a mark" in breach.mark_ambiguity
    assert f'ratchet move --function {PATH} "{OLD}" "{TWIN}"' in breach.mark_ambiguity
    assert result.carried == ()


def test_the_marks_are_fetched_at_most_once_and_never_with_no_breach():
    over, fetch = judged(NEW, {(PATH, NEW), (PATH, "grade( x )")},
                         {(PATH, OLD): 7.0, (PATH, "grade( y )"): 3.0}, CrapBound(7.0, 7.0))
    under, idle = judged(NEW, {(PATH, NEW)}, {(PATH, OLD): 7.0}, CrapBound(5.0, 5.0))

    assert (fetch.calls, len(over.carried)) == (1, 1)
    assert (idle.calls, under) == (0, gate.GateResult(judged=1))


def test_a_carried_function_under_its_ceiling_yields_no_carried_finding():
    """The marks callable runs only on a breach, so nothing asks about a carry."""
    result, fetch = judged(NEW, {(PATH, NEW)}, {(PATH, OLD): 7.0}, CrapBound(6.0, 6.0))

    assert (result.carried, fetch.calls) == ((), 0)


def _kind(result: gate.GateResult) -> str:
    """Which list holds the one breach, a carry read as the pardon it is."""
    (found,) = [name for name in ("over_ceiling", "pardoned", "marked_rise", "unproven", "carried")
                if getattr(result, name)]
    return "pardoned" if found == "carried" else found


BOUNDS = [CrapBound(low, high) for low, high in itertools.combinations_with_replacement(
    (6.5, 7.0, 7.00004, 7.0001, 9.0, 72.0), 2)]


@pytest.mark.parametrize("bound", BOUNDS, ids=str)
@pytest.mark.parametrize("mark", (7.0, 7.0001, 9.0, 72.0))
def test_a_carry_changes_which_key_holds_the_mark_never_the_verdict(bound, mark):
    """Check 3: the verdict on a carried function equals its verdict with the
    same mark written under its own key."""
    carried, _ = judged(NEW, {(PATH, NEW)}, {(PATH, OLD): mark}, bound)
    own, _ = judged(NEW, {(PATH, NEW)}, {(PATH, NEW): mark}, bound)

    assert _kind(carried) == _kind(own)
    assert [b.mark for b in carried.carried + carried.marked_rise + carried.unproven] == \
        [b.mark for b in own.pardoned + own.marked_rise + own.unproven]


# --- the base a carried function is asked about ----------------------------------
#
# A measured scope: a staged function's CRAP runs from its ccn to ccn^2 + ccn,
# and a ceiling of 3 puts ccn 4 over it. The diff base holds the function under
# the key its mark was recorded under, never under the key the edit gave it.

class BaseFetch:
    """The base fetch over {(path, key name): ccn}, recording each call's keys."""

    def __init__(self, ccns: dict):
        self.ccns, self.asked = ccns, []

    def __call__(self, asked):
        self.asked.append(asked)
        return {key: self.ccns.get(key) for key in asked}


def judged_at_base(name: str, marked: dict, bound: CrapBound, base: dict):
    """The gate's result when NAME, the one function of PATH, sits at BOUND,
    MARKED holds the marks and BASE the ccn at the diff base."""
    fetch = BaseFetch(base)
    change = ChangedFile(PATH, WHOLE, (Function(name, 1, 6, bound),))
    return gate.judge([change], lambda scope: 3, Marks(marked), fetch), fetch


@pytest.mark.parametrize("mark, bound, kind, asked", [
    pytest.param(20.0, CrapBound(4, 20), "carried", [], id="at-its-mark-is-pardoned-and-never-asked"),
    pytest.param(15.0, CrapBound(4, 20), "unproven", [((PATH, OLD),)], id="straddling-at-its-base-ccn"),
    pytest.param(20.0, CrapBound(5, 30), "unproven_rise", [((PATH, OLD),)], id="straddling-past-its-base-ccn"),
])
def test_a_carried_function_is_asked_about_under_the_key_its_mark_carried_from(mark, bound, kind, asked):
    """The fetch runs once per `gate.judge` call, with the carried key alone:
    asked under the new key it found no function and read every straddling
    carried function as a rise."""
    result, fetch = judged_at_base(NEW, {(PATH, OLD): mark}, bound, {(PATH, OLD): 4})

    (breach,) = getattr(result, kind)
    assert (breach.carried_from, breach.mark, fetch.asked) == (OLD, mark, asked)
    assert breach.base_ccn == (4 if asked else None)


@pytest.mark.parametrize("bound", [CrapBound(4, 20), CrapBound(5, 30), CrapBound(3.5, 12)], ids=str)
@pytest.mark.parametrize("base", [3, 4, 5, None])
@pytest.mark.parametrize("mark", [3.5, 15.0, 20.0, 30.0])
def test_a_carried_function_rises_as_it_would_under_its_own_key(bound, base, mark):
    """Check 3 for the commit gate: with the same mark and the same base ccn
    under the key that holds them, a carry and an exact mark give one verdict."""
    carried, _ = judged_at_base(NEW, {(PATH, OLD): mark}, bound, {(PATH, OLD): base})
    own, _ = judged_at_base(NEW, {(PATH, NEW): mark}, bound, {(PATH, NEW): base})

    kinds = ("over_ceiling", "marked_rise", "unproven", "unproven_rise")
    assert [len(getattr(carried, kind)) for kind in kinds] == [len(getattr(own, kind)) for kind in kinds]
    assert (len(carried.carried), [b.base_ccn for b in carried.unproven + carried.unproven_rise]) == \
        (len(own.pardoned), [b.base_ccn for b in own.unproven + own.unproven_rise])
