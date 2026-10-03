"""The gate module's hand table: which changed functions a gate judges, and the pardon.

One row per rule, each read through `gate.judge`. Below the table, two
differentials hold verify's gate to the 0.8.1 tree's: an exhaustive grid that
runs everywhere, and a hypothesis search over (ccn, cov, mark, spans) where
hypothesis is installed (the accuracy extras). Both compare
`verify.evaluate(...).gate_violations`, which now judges through the gate, with
a frozen copy of 0.8.1's `verify._gate_violations`, kept here for this slot.

The case builder is this module's public part: gate-group-07 and gate-group-08
import `fn`, `changed`, `unread`, `claimed`, `Marks` and `judged` to build their
adapters' mapping cases.

    result, marks = judged(changed("src/a.py", fn(low=8, high=72)), marks={("src/a.py", "f( x )"): 8.0})
"""
from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import NamedTuple

import pytest

from crapkit import gate
from crapkit.keys import MarkIndex, key_names, key_of
from crapkit.ratchet import RatchetEntry
from crapkit.score import CRAP_PLACES, ScoredRow, crap, over_ceiling
from crapkit.verify import GateViolation, evaluate

CEILING = 6
NAME = "f( x )"
PATH = "src/a.py"


# --- the case builder -------------------------------------------------------------

def fn(name: str = NAME, start: int = 1, end: int = 9, *, low: float = 10.0, high: float | None = None,
       scope: str = "src", occurrence: int = 0, record=None) -> gate.Function:
    """One function the gate may judge. `high` defaults to `low`: the exact CRAP."""
    return gate.Function(name, start, end, gate.CrapBound(low, low if high is None else high),
                         scope, occurrence, record)


def changed(path: str, *functions: gate.Function, spans=gate.WHOLE) -> gate.ChangedFile:
    """A changed file holding these functions; WHOLE spans judge every one."""
    return gate.ChangedFile(path, spans, tuple(functions))


def unread(path: str = "src/b.ts", reason: str = "src/b.ts:12: arrow refused", *, spans=gate.WHOLE,
           dirty: bool = False) -> gate.ChangedFile:
    """A file no reader could read; spans () is one the change never touched."""
    return gate.ChangedFile(path, spans, gate.Unread(path, reason, dirty))


def claimed(path: str = "src/caf\udce9.py", scope: str = "src", *, spans=(),
            dirty: bool = False) -> gate.ChangedFile:
    """A file a scope takes whose name is not UTF-8, in git's surrogateescape spelling."""
    return gate.ChangedFile(path, spans, gate.UnreadableName(path, scope, dirty))


class _Mark(NamedTuple):
    path: str
    long_name: str
    crap: float


@dataclass
class Marks:
    """The marks callable: a MarkIndex over {(path, key name): mark}, counting its calls."""
    marks: Mapping[tuple[str, str], float] = field(default_factory=dict)
    calls: int = 0

    def __call__(self) -> MarkIndex:
        self.calls += 1
        return MarkIndex(_Mark(path, name, mark) for (path, name), mark in self.marks.items())


def judged(*changes: gate.ChangedFile, ceiling: int | Mapping[str, int] = CEILING,
           marks: Mapping[tuple[str, str], float] | None = None) -> tuple[gate.GateResult, Marks]:
    """The gate's result on these changes, and the marks callable it was handed.
    `ceiling` is one ceiling for every scope, or each scope's."""
    ceilings = ceiling.__getitem__ if isinstance(ceiling, Mapping) else (lambda scope: ceiling)
    fetch = Marks(marks or {})
    return gate.judge(changes, ceilings, fetch), fetch


def names(found) -> list[str]:
    return [finding.key_name for finding in found]


# --- touch -------------------------------------------------------------------------

def test_an_untouched_function_over_the_ceiling_yields_nothing():
    result, _ = judged(changed(PATH, fn(start=20, end=30, low=40.0), spans=[(1, 5)]))

    assert result == gate.GateResult()


def test_a_span_that_meets_a_function_on_its_first_or_last_line_touches_it():
    function = fn(start=10, end=20)

    assert [gate.touches(function, spans) for spans in ([(1, 10)], [(20, 25)], [(1, 9)], [(21, 30)], [])] == [
        True, True, False, False, False]


def test_touched_at_the_ceiling_passes_and_touched_over_it_by_low_fails():
    at, over = fn("at( )", 1, 9, low=6.0, high=40.0), fn("over( )", 10, 19, low=6.5)

    result, _ = judged(changed(PATH, at, over))

    assert names(result.over_ceiling) == ["over( )"]
    assert result.judged == 2


def test_each_function_is_held_to_its_own_scopes_ceiling():
    lib, src = fn("lib( )", 1, 9, low=10.0, scope="lib"), fn("src( )", 10, 19, low=10.0)

    result, _ = judged(changed(PATH, lib, src), ceiling={"lib": 12, "src": 6})

    assert names(result.over_ceiling) == ["src( )"]


def test_a_crap_a_float_puts_a_hair_over_its_ceiling_is_at_it():
    """A CRAP whose double lands a hair above its whole-number ceiling is at it:
    score.over_ceiling decides for the exact value, as verify always has."""
    hair = 6 * (1 + 2 ** -50)

    result, _ = judged(changed(PATH, fn(low=hair)))

    assert result.over_ceiling == ()


def test_a_whole_file_judges_every_function():
    functions = [fn(f"g{n}( )", 10 * n + 1, 10 * n + 9, low=8.0) for n in range(3)]

    result, _ = judged(changed(PATH, *functions, spans=gate.WHOLE))

    assert names(result.over_ceiling) == ["g0( )", "g1( )", "g2( )"]
    assert all(gate.touches(f, gate.WHOLE) for f in functions)


# --- the pardon ----------------------------------------------------------------------

def _pardon(low: float, high: float, mark: float) -> gate.GateResult:
    result, _ = judged(changed(PATH, fn(low=low, high=high)), marks={(PATH, NAME): mark})
    return result


def test_exact_crap_at_the_mark_at_4_places_is_pardoned():
    result = _pardon(12.00004, 12.00004, 12.0)

    assert (names(result.pardoned), result.over_ceiling, result.marked_rise, result.unproven) == (
        [NAME], (), (), ())
    assert result.pardoned[0].mark == 12.0


def test_exact_crap_above_the_mark_is_a_marked_rise():
    result = _pardon(12.00006, 12.00006, 12.0)

    assert (names(result.marked_rise), result.pardoned, result.unproven) == ([NAME], (), ())


def test_bounds_that_straddle_the_mark_are_unproven():
    result = _pardon(8.0, 72.0, 63.6)

    assert (names(result.unproven), result.pardoned, result.marked_rise) == ([NAME], (), ())


@pytest.mark.parametrize(("low", "high", "mark", "kind"), [
    (8.0, 72.0, 72.0, "pardoned"),
    (8.0, 72.0, 8.0, "unproven"),
    (8.0, 72.0, 7.9999, "marked_rise"),
    (8.0, 8.0, 8.0, "pardoned"),
    (8.0, 8.0, 7.0, "marked_rise"),
], ids=["high-at-mark", "low-at-mark", "low-over-mark", "cc-only-at", "cc-only-over"])
def test_the_bound_sorts_a_marked_function_by_its_two_ends(low, high, mark, kind):
    result = _pardon(low, high, mark)

    found = {name: names(getattr(result, name)) for name in ("pardoned", "marked_rise", "unproven")}
    assert found == {name: [NAME] if name == kind else [] for name in found}


def test_a_function_no_mark_covers_is_over_the_ceiling_with_no_mark():
    result, _ = judged(changed(PATH, fn(low=20.0)), marks={("src/other.py", NAME): 99.0})

    assert names(result.over_ceiling) == [NAME]
    assert result.over_ceiling[0].mark is None


def test_the_marks_are_never_fetched_when_nothing_is_over_the_ceiling():
    result, marks = judged(changed(PATH, fn(low=6.0), fn("g( )", 10, 19, low=40.0), spans=[(1, 2)]),
                           unread(spans=()))

    assert (result, marks.calls) == (gate.GateResult(judged=1), 0)


def test_the_marks_are_fetched_once_however_many_functions_need_a_pardon():
    files = [changed(f"src/{n}.py", fn(low=10.0), fn("g( )", 10, 19, low=12.0)) for n in "abc"]

    result, marks = judged(*files, marks={("src/a.py", NAME): 99.0})

    assert (marks.calls, len(result.over_ceiling), len(result.pardoned)) == (1, 5, 1)


# --- twin keys --------------------------------------------------------------------------

def test_twin_keys_come_from_the_whole_files_functions_not_the_breaching_subset():
    """Only the second twin breaches; keyed over the breaching subset alone it
    would read as the first and take the first twin's mark."""
    first, second = fn(start=1, end=9, low=2.0), fn(start=20, end=29, low=30.0)

    result, _ = judged(changed(PATH, first, second), marks={(PATH, NAME): 40.0, (PATH, f"{NAME}#2"): 25.0})

    assert names(result.marked_rise) == [f"{NAME}#2"]
    assert result.marked_rise[0].mark == 25.0


def test_scope_copies_of_one_function_share_its_key():
    copies = [fn(start=5, end=9, low=10.0, scope=scope, occurrence=1) for scope in ("src", "lib")]

    result, _ = judged(changed(PATH, *copies))

    assert names(result.over_ceiling) == [NAME, NAME]


def test_a_finding_hands_back_the_callers_own_record():
    record = object()

    result, _ = judged(changed(PATH, fn(low=10.0, record=record)))

    assert result.over_ceiling[0].function.record is record
    assert (result.over_ceiling[0].path, result.over_ceiling[0].ceiling) == (PATH, CEILING)


# --- unread files and unreadable names ----------------------------------------------------

def test_a_changed_unread_file_yields_unread():
    result, _ = judged(unread("src/b.ts", "why", spans=[(3, 4)]), unread("src/c.ts", "why", dirty=True))

    assert result.unread == (gate.Unread("src/b.ts", "why"), gate.Unread("src/c.ts", "why", True))


def test_an_unread_file_the_change_never_touched_yields_nothing():
    result, _ = judged(unread(spans=()))

    assert result == gate.GateResult()


@pytest.mark.parametrize("spans", [(), [(1, 1)], gate.WHOLE], ids=["no-spans", "one-span", "whole"])
def test_an_unreadable_name_yields_unreadable_name_whatever_its_spans(spans):
    result, _ = judged(claimed(spans=spans))

    assert result.unreadable_name == (gate.UnreadableName("src/caf\udce9.py", "src"),)


def test_the_gate_judges_no_function_when_an_unreadable_name_is_present():
    breach, rise = changed(PATH, fn(low=40.0)), changed("src/m.py", fn(low=40.0))

    result, marks = judged(breach, unread(), claimed(dirty=True), rise, claimed("src/d\udce9.py", "lib"),
                           marks={("src/m.py", NAME): 1.0})

    assert result == gate.GateResult(unreadable_name=(gate.UnreadableName("src/caf\udce9.py", "src", True),
                                                      gate.UnreadableName("src/d\udce9.py", "lib")))
    assert marks.calls == 0


def test_the_module_imports_only_the_standard_library_and_keys_at_module_scope():
    import ast
    import sys
    from pathlib import Path

    tree = ast.parse(Path(gate.__file__).read_text(encoding="utf-8"))
    imported = [node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)]
    imported += [alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names]

    local = [name for name in imported if name not in ("__future__", "keys")]
    assert [name for name in local if name.split(".")[0] not in sys.stdlib_module_names] == []


# --- the differential against 0.8.1's verify gate ---------------------------------------
# A frozen copy of the 0.8.1 tree's verify._gate_violations and the helpers it
# called, for this slot only. One mark per key: 0.8.1 kept the last of two
# marks under one key, which gate-group-02 changed on purpose to the first.

def _touched_081(row, ranges):
    spans = ranges.get(row.path)
    if not spans:
        return False
    return any(not (hi < row.start or lo > row.end) for lo, hi in spans)


def _ceiling_081(row, target, scope_targets):
    return (scope_targets or {}).get(row.scope, target)


def _within_mark_081(row, key, marks):
    mark = marks.get(key)
    return mark is not None and round(row.crap, 4) <= mark


def _worst_first_081(row):
    return -round(row.crap, CRAP_PLACES), row.path, row.start


def _gate_violations_081(fresh, changed_ranges, target, scope_targets, dirty, ratchet):
    names_ = key_names(fresh)
    marks = {(e.path, e.long_name): e.crap for e in ratchet}
    found = [
        GateViolation(r.path, r.long_name, r.start, r.ccn, r.cov, r.crap, r.remedy,
                      r.path in dirty, key_of(names_, r)[1])
        for r in fresh
        if over_ceiling(r.crap, _ceiling_081(r, target, scope_targets)) and _touched_081(r, changed_ranges)
        and not _within_mark_081(r, key_of(names_, r), marks)
    ]
    found.sort(key=_worst_first_081)
    return found


def _row(path: str, name: str, start: int, ccn: int, cov: float, scope: str = "src") -> ScoredRow:
    score = crap(ccn, cov)
    return ScoredRow(scope, path, name, start, start + 4, ccn, ccn, ccn, 5, 1, 1, cov, "measured", score,
                     "decompose" if ccn > CEILING else "add-tests", occurrence=1)


def _differential(rows, ranges, marks, dirty=frozenset(), scope_targets=None) -> list[GateViolation]:
    """verify's gate violations through the gate, held equal to 0.8.1's, order included."""
    ratchet = [RatchetEntry(path, name, mark) for (path, name), mark in marks.items()]
    through_gate = evaluate(fresh=rows, changed_ranges=ranges, ratchet=ratchet, baseline_failures=set(),
                            fresh_failures=set(), target=CEILING, scope_targets=scope_targets,
                            dirty_paths=set(dirty)).gate_violations

    assert through_gate == _gate_violations_081(rows, ranges, CEILING, scope_targets, set(dirty), ratchet)
    return through_gate


COVS = [Fraction(k, 6) for k in range(7)]
CCNS = [1, 2, 3, 5, 6, 7, 9, 13]
# The first function spans lines 1-5 and its twin 20-24.
SPANS = {"no-change": {}, "empty": {PATH: []}, "def-line": {PATH: [(1, 1)]}, "last-line": {PATH: [(5, 5)]},
         "between": {PATH: [(6, 19)]}, "twin-last-line": {PATH: [(24, 24)]}, "all": {PATH: [(1, 100)]},
         "other-file": {"src/b.py": [(1, 100)]}}


@pytest.mark.parametrize("spans", SPANS.values(), ids=SPANS.keys())
def test_verify_through_the_gate_equals_0_8_1_on_the_grid(spans):
    """Every ccn and cov of the grid, each against no mark and a mark at, under,
    over and half its 4dp CRAP, with the twin beside it marked the same."""
    for ccn, cov in itertools.product(CCNS, COVS):
        rows = [_row(PATH, NAME, 1, ccn, float(cov)), _row(PATH, NAME, 20, ccn + 1, float(cov))]
        exact = round(rows[0].crap, 4)
        for mark in (None, exact, exact - 0.0001, exact + 0.0001, round(exact / 2, 4)):
            marks = {} if mark is None else {(PATH, NAME): mark, (PATH, f"{NAME}#2"): mark}
            _differential(rows, spans, marks, dirty={PATH} if ccn % 2 else set())


@pytest.mark.parametrize("marked", ["a( x )", "b( y )"])
def test_verify_lists_rows_that_tie_on_one_start_line_in_the_runs_order(marked):
    """Two functions start on line 3 with one CRAP, 90 at 4 places. One is marked
    under it, a marked rise; the other has no mark. 0.8.1 listed a tie in the
    order the run listed the rows, whichever kind of finding each is."""
    rows = [_row(PATH, "a( x )", 3, 9, 0.0)._replace(occurrence=1),
            _row(PATH, "b( y )", 3, 9, 0.0)._replace(occurrence=2)]

    found = _differential(rows, {PATH: [(1, 100)]}, {(PATH, marked): 89.0})

    assert [(v.long_name, v.crap) for v in found] == [("a( x )", 90.0), ("b( y )", 90.0)]


def test_verify_through_the_gate_equals_0_8_1_on_drawn_cases():
    """hypothesis over (ccn, cov, mark, spans), several functions and files,
    two scopes with their own ceilings. Skipped where the accuracy extras are
    not installed: CI's unit jobs install the dev extra only.

    Functions start on lines 1-12 and spans begin on lines 0-12, so functions
    often share a start line and a span reaches them: a tie on one line
    between a marked rise and a function with no mark is drawn, and the order
    0.8.1 listed it in is held."""
    hypothesis = pytest.importorskip("hypothesis")
    st = hypothesis.strategies

    function = st.tuples(st.sampled_from(["src/a.py", "src/b.py"]), st.sampled_from(["f( x )", "g( )"]),
                         st.integers(1, 12), st.integers(1, 30), st.integers(0, 60),
                         st.sampled_from(["src", "lib"]))
    span = st.tuples(st.integers(0, 12), st.integers(0, 20)).map(lambda pair: (pair[0], pair[0] + pair[1]))

    @hypothesis.settings(max_examples=1000, deadline=None, derandomize=True, database=None)
    @hypothesis.given(st.lists(function, max_size=8),
                      st.dictionaries(st.sampled_from(["src/a.py", "src/b.py", "src/c.py"]),
                                      st.lists(span, max_size=3), min_size=1, max_size=3),
                      st.lists(st.tuples(st.integers(0, 7), st.floats(0, 2000, allow_nan=False)), max_size=4),
                      st.sets(st.sampled_from(["src/a.py", "src/b.py"])))
    def check(functions, ranges, marked, dirty):
        rows = _on_their_lines(functions)
        keyed = key_names(rows)
        keys = sorted({key_of(keyed, row) for row in rows})
        marks = {keys[index % len(keys)]: round(mark, 4) for index, mark in marked} if keys else {}
        _differential(rows, ranges, marks, dirty, scope_targets={"lib": 12})

    check()


def _on_their_lines(functions) -> list[ScoredRow]:
    """Rows as one analysis writes them: functions that start on one line each
    carry their order on it (keys.position), 1 for the first."""
    on_line: Counter = Counter()
    rows = []
    for path, name, start, ccn, cov, scope in functions:
        on_line[path, start] += 1
        rows.append(_row(path, name, start, ccn, cov / 60, scope)._replace(occurrence=on_line[path, start]))
    return rows


# --- verify's adapter --------------------------------------------------------------------

VERIFY_CONFIG = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def _verify_stop(monkeypatch, tmp_path, tracked: list[str], dirty: set[str]):
    """verify's claimed-name stop over `tracked`, ready to run, and the list it
    fills with the changes it hands the gate."""
    from crapkit import gitio
    from crapkit.cli import verifying
    from crapkit.config import load_config_text

    handed, real = [], gate.judge

    def spy(changes, ceilings, marks):
        handed.append(tuple(changes))
        return real(handed[-1], ceilings, marks)

    monkeypatch.setattr(gitio, "ls_files", lambda root: tracked)
    monkeypatch.setattr(gate, "judge", spy)
    return handed, lambda: verifying._stop_on_claimed_names(tmp_path, load_config_text(VERIFY_CONFIG), dirty)


def test_verify_hands_each_claimed_name_to_the_gate_whole_and_stops_on_its_finding(monkeypatch, tmp_path):
    from crapkit.errors import UNREAD_NAME_REASON, UnreadableNameError

    tracked = ["src/app.py", "src/o\udc92brien.py", "docs/caf\udce9.md", "src/caf\udce9.py"]
    handed, stop = _verify_stop(monkeypatch, tmp_path, tracked, {"src/o\udc92brien.py"})

    with pytest.raises(UnreadableNameError) as refused:
        stop()

    assert handed == [(
        gate.ChangedFile("src/caf\udce9.py", gate.WHOLE, gate.UnreadableName("src/caf\udce9.py", "src")),
        gate.ChangedFile("src/o\udc92brien.py", gate.WHOLE, gate.UnreadableName("src/o\udc92brien.py", "src", True)))]
    assert str(refused.value) == ("src/caf\\xe9.py (and 1 more) is in scope 'src', but git names it in bytes "
                                  "that are not UTF-8 and crapkit reads every path as UTF-8; a file a scope takes "
                                  "is refused, not left out, so no gate passes it unread: rename it (git mv) to a "
                                  "UTF-8 name")
    assert refused.value.json_fields() == {"unread_files": [
        {"path": "src/caf\\xe9.py", "reason": UNREAD_NAME_REASON, "dirty": False},
        {"path": "src/o\\x92brien.py", "reason": UNREAD_NAME_REASON, "dirty": True}]}


def test_verify_asks_the_gate_nothing_when_no_scope_takes_an_unreadable_name(monkeypatch, tmp_path):
    handed, stop = _verify_stop(monkeypatch, tmp_path, ["src/app.py", "docs/caf\udce9.md"], set())

    stop()

    assert handed == []
