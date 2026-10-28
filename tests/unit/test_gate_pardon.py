"""The pardon, at the bound hook-precommit hands the gate.

A staged blob carries no coverage, so what the hook knows of a function's CRAP
is a range: ccn at full coverage up to the CRAP at coverage 0 under its scope's
flag. `gate.judge` sorts a marked function over its ceiling by that range
against its mark, each end at the 4 places a mark is stored at: pardoned when
the high end sits at or under the mark, a marked rise when the low end sits
above it, unproven when the two ends straddle it. hook-precommit refuses a
marked rise (cli/verifying maps it); the other two pass.

Every bound here comes from `hook._changed_file`, the function that builds the
hook's rows, so the table judges what the hook hands the gate. The property at
the bottom holds the classes to the exact CRAP at every coverage a lane can
report in 60ths.
"""
from __future__ import annotations

import pytest

from test_gate_cases import Marks

from accuracy.kit import exact
from crapkit import gate, hook
from crapkit.config import load_config_text
from crapkit.merge import FunctionRecord
from crapkit.score import CRAP_PLACES, flagged_crap

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[scope]]
name = "cc"
paths = ["cc"]
languages = ["python"]
coverage_optional = true

[[scope]]
name = "bare"
paths = ["bare"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -m pytest"
artifact = "coverage.json"
parser = "coveragepy"
scopes = ["src"]
"""
CFG = load_config_text(CONFIG)
FLAGS = {"src": "untested", "cc": "cc-only"}
NAME = "read_row( line , cols )"
KINDS = ("over_ceiling", "pardoned", "marked_rise", "unproven")


def record(ccn: int, name: str = NAME, start: int = 1, path: str = "src/a.py") -> FunctionRecord:
    return FunctionRecord(path, name, start, start + 9, ccn, ccn, ccn, 10, 2, 1)


def staged(scope: str, *records: FunctionRecord) -> gate.ChangedFile:
    """The staged file as hook-precommit builds it, every function touched."""
    return hook._changed_file(f"{scope}/a.py", list(records), gate.WHOLE, scope, CFG, {})


def kinds(result: gate.GateResult) -> dict[str, str]:
    """Each breach's key name with the kind of finding the gate made of it."""
    return {breach.key_name: kind for kind in KINDS for breach in getattr(result, kind)}


def judge(change: gate.ChangedFile, marks: dict, ceiling: int = 6) -> tuple[gate.GateResult, Marks]:
    fetch = Marks(marks)
    return gate.judge([change], lambda scope: ceiling, fetch), fetch


# --- one row per case -------------------------------------------------------------

ROWS = [
    # (scope, ccn, mark, kind): measured ccn 8 is the range (8, 72), cc-only (8, 8).
    pytest.param("src", 8, 7.3333, "marked_rise", id="measured-8-mark-7.3333"),
    pytest.param("src", 8, 7.9999, "marked_rise", id="measured-8-mark-7.9999"),
    pytest.param("src", 8, 8.0, "unproven", id="measured-8-mark-8-ccn-at-the-mark-is-no-rise"),
    pytest.param("src", 8, 63.6, "unproven", id="measured-8-mark-63.6"),
    pytest.param("src", 8, 72.0, "pardoned", id="measured-8-mark-72"),
    pytest.param("cc", 8, 7.0, "marked_rise", id="cc-only-8-mark-7"),
    pytest.param("cc", 8, 8.0, "pardoned", id="cc-only-8-mark-8"),
    pytest.param("src", 8, None, "over_ceiling", id="measured-8-no-mark"),
]


@pytest.mark.parametrize(("scope", "ccn", "mark", "kind"), ROWS)
def test_each_range_against_its_mark_is_one_kind_of_finding(scope, ccn, mark, kind):
    change = staged(scope, record(ccn, path=f"{scope}/a.py"))
    marks = {} if mark is None else {(f"{scope}/a.py", NAME): mark}

    result, fetch = judge(change, marks)

    assert kinds(result) == {NAME: kind}
    assert fetch.calls == 1
    assert [breach.mark for kind_ in KINDS for breach in getattr(result, kind_)] == [mark]


def test_a_second_twin_is_judged_by_its_own_mark():
    """Two functions of one name: the first twin's mark of 80 would pardon a
    (8, 72) range, and the second twin's mark of 7 is what judges it."""
    first, second = record(8, start=1), record(8, start=20)
    marks = {("src/a.py", NAME): 80.0, ("src/a.py", f"{NAME}#2"): 7.0}

    result, _ = judge(staged("src", first, second), marks)

    assert kinds(result) == {NAME: "pardoned", f"{NAME}#2": "marked_rise"}
    assert [(b.key_name, b.mark, b.function.start) for b in result.marked_rise] == [(f"{NAME}#2", 7.0, 20)]


def test_a_clean_change_never_calls_the_marks_fetch():
    result, fetch = judge(staged("src", record(6), record(3, "coerce( word )", 20)), {("src/a.py", NAME): 7.0})

    assert result == gate.GateResult(judged=2)
    assert fetch.calls == 0


# --- the range rows: the bound the hook builds ------------------------------------

@pytest.mark.parametrize(("scope", "ccn", "bound"), [
    pytest.param("cc", 8, gate.CrapBound(8, 8.0), id="cc-only-ccn-8"),
    pytest.param("src", 8, gate.CrapBound(8, 72.0), id="measured-ccn-8"),
    pytest.param("src", 1, gate.CrapBound(1, 2.0), id="measured-ccn-1"),
    pytest.param("bare", 8, gate.CrapBound(8, 72.0), id="lane-less-scope-without-coverage-optional"),
])
def test_the_hook_bounds_a_staged_function_by_its_scopes_flag(scope, ccn, bound):
    (function,) = staged(scope, record(ccn, path=f"{scope}/a.py")).content

    assert function.bound == bound


@pytest.mark.parametrize("scope", sorted(FLAGS))
def test_the_high_end_is_the_crap_at_coverage_0_and_its_closed_form(scope):
    """ccn^2 + ccn where a lane measures coverage, ccn in a cc-only scope: the
    accuracy kit's exact CRAP at cov 0, and score's own CRAP under the flag."""
    closed = (lambda ccn: exact.crap(ccn, 0)) if scope == "src" else (lambda ccn: ccn)
    records = [record(ccn, f"f{ccn}( x )", 10 * ccn, f"{scope}/a.py") for ccn in range(1, 61)]

    bounds = [function.bound for function in staged(scope, *records).content]

    assert [b.high for b in bounds] == [flagged_crap(ccn, 0.0, FLAGS[scope]) for ccn in range(1, 61)]
    assert [b.high for b in bounds] == [float(closed(ccn)) for ccn in range(1, 61)]
    assert [b.low for b in bounds] == list(range(1, 61))


# --- the property: a refusal and a pardon hold at every coverage ------------------

COVS = [k / 60 for k in range(61)]


def exact_craps(ccn: int, flag: str) -> list[float]:
    """The CRAP a lane could report for this function, at 4 places, for each
    coverage in 60ths: what verify compares with the mark."""
    return [round(flagged_crap(ccn, cov, flag), CRAP_PLACES) for cov in COVS]


def candidate_marks(craps: list[float]) -> list[float]:
    """Integers, 4 dp values one step either side, and the exact tie values:
    the CRAP at each coverage, which is where a wrong comparison would show."""
    step = 10 ** -CRAP_PLACES
    near = {round(value + delta, CRAP_PLACES) for value in craps for delta in (-step, 0.0, step)}
    whole = {float(n) for value in craps for n in (int(value) - 1, int(value), int(value) + 1)}
    return sorted(near | whole)


@pytest.mark.parametrize("scope", sorted(FLAGS))
def test_a_rise_and_a_pardon_hold_at_every_coverage(scope):
    """Over ccn 1 to 40: a marked rise means the CRAP rounds above the mark at
    every coverage, so verify reports the rise whatever the tests reach; a
    pardon means it rounds at or under the mark at every coverage."""
    path, checked = f"{scope}/a.py", {"marked_rise": 0, "pardoned": 0, "unproven": 0}
    for ccn in range(1, 41):
        craps = exact_craps(ccn, FLAGS[scope])
        marks = candidate_marks(craps)
        records = [record(ccn, f"f{i}( x )", 10 * i + 1, path) for i in range(len(marks))]
        result, _ = judge(staged(scope, *records), {(path, f"f{i}( x )"): m for i, m in enumerate(marks)},
                          ceiling=0)
        for name, kind in kinds(result).items():
            mark = marks[int(name[1:name.index("(")])]
            checked[kind] += 1
            assert kind != "marked_rise" or all(crap > mark for crap in craps), (ccn, mark)
            assert kind != "pardoned" or all(crap <= mark for crap in craps), (ccn, mark)

    # A cc-only range is one point, so nothing there straddles its mark.
    assert {kind for kind, count in checked.items() if count} == (
        {"marked_rise", "pardoned", "unproven"} if scope == "src" else {"marked_rise", "pardoned"}), checked
