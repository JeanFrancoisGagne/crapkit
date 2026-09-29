"""crapkit.invariants at its edges: every stop's exact words, each site's tally,
the bounds at their exact ends, and the receipt a process writes.

A stop's text is message(check, at, kept): the check the bound comes from, the
row or count it caught with the numbers it read, and what the stop kept from
happening. Each check adds one call and the nanoseconds between its start and
its end to its site's tally.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from crapkit import invariants as inv
from crapkit.churn import FileChurn
from crapkit.errors import InternalCheckError
from crapkit.merge import FunctionRecord
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow
from crapkit.verify import GateViolation, Verdict
from crapkit.worklist import WorklistEntry

AT = "src/a.py:f( x )"


def _entry(path="src/a.py", commits=2, authors=1, weight=0.6, risk=4.2, remedy="decompose"):
    return WorklistEntry("src", path, "f( x )", 3, 12, 7, 7, 8, commits, authors, weight,
                         risk, "measured", remedy, 7.0, 1.0)


def _row(**over) -> ScoredRow:
    """ccn 4 at cov 0.5 is CRAP 16 x 0.125 + 4 = 6.0, which is ok at ceiling 6."""
    base = dict(scope="src", path="src/a.py", long_name="f( x )", start=3, end=12, ccn_std=4,
                ccn_mod=4, ccn=4, nloc=8, params=1, nesting=2, cov=0.5, flag="measured",
                crap=6.0, remedy="ok", cognitive=5, occurrence=1, inline_body=0)
    return ScoredRow(**{**base, **over})


def _record(**over) -> FunctionRecord:
    base = dict(path="src/a.py", long_name="f( x )", start=3, end=12, ccn_std=4, ccn_mod=4,
                ccn=4, nloc=8, params=1, nesting=2, cognitive=5, occurrence=1, inline_body=0)
    return FunctionRecord(**{**base, **over})


def _mark(value: float) -> RatchetEntry:
    return RatchetEntry("src/a.py", "f( x )", value)


def _violation(ccn: int) -> GateViolation:
    return GateViolation("src/a.py", "f( x )", 3, ccn, 1.0, float(ccn), "decompose")


def _at(e) -> str:
    return (f"{e.path}:{e.long_name} (commits {e.commits}, authors {e.authors}, "
            f"weight {e.weight!r}, risk {e.risk!r})")


def _stops(call) -> str:
    with pytest.raises(InternalCheckError) as caught:
        call()
    return str(caught.value)


LOW_RISK, HIGH_RISK = _entry(risk=1.0), _entry(path="src/b.py", risk=2.0)
NO_COMMITS = _entry(commits=0, authors=0, weight=0.0)
TOO_MANY_AUTHORS = _entry(authors=3)
HEAVY = _entry(commits=1, weight=0.9)
ROW_7 = _row(ccn_std=7, ccn_mod=7, ccn=7, cov=1.0, crap=7.0, remedy="ok")
VERDICT = Verdict(True, [], [], [], [], [], [], [], [])
SUMMARY = {"run_id": 7, "functions": 3, "measured": 3, "untested": 0, "excluded": 0,
           "no_lane": 0, "cc_only": 0, "over_target": 0, "grade": "A+", "crap_load": 3.0}


@pytest.mark.parametrize("call, check, at, kept", [
    (lambda: inv._check_churn([NO_COMMITS], [], {}),
     "active rows have commits in the window and dormant rows have none", _at(NO_COMMITS),
     inv.PRINTED),
    (lambda: inv._check_churn([TOO_MANY_AUTHORS], [], {}),
     "churn must satisfy 0 <= authors <= commits and 0 <= weight <= commits",
     _at(TOO_MANY_AUTHORS), inv.PRINTED),
    (lambda: inv._check_churn([HEAVY], [], {"a.py": FileChurn(1, 1, 0.9)}),
     "a commit weighs at most 0.5 unless every commit shares one timestamp",
     "src/a.py (commits 1, weight 0.9)", inv.PRINTED),
    (lambda: inv._check_risk_order([LOW_RISK, HIGH_RISK]), "the worklist ranks by risk, highest first",
     f"{_at(HIGH_RISK)} ranks under {_at(LOW_RISK)}", inv.PRINTED),
    (lambda: inv.check_violations([_violation(6)], {"src": ["src/a.py"]}, lambda scope: 6),
     "a gate violation has ccn over its file's ceiling",
     f"{AT} (ccn 6, ceiling 6, hook-precommit)", inv.PRINTED),
    (lambda: inv.check_gate([_row()], [_violation(6)], {"src": 6}.__getitem__),
     "a gate violation has ccn over its file's ceiling",
     f"{AT} (ccn 6, ceiling 6, rescore --gate)", inv.PRINTED),
    (lambda: inv.check_gate([_row(crap=99.0)], [], lambda scope: 6),
     "CRAP must lie between ccn and ccn^2+ccn", inv.where(_row(crap=99.0)), inv.PRINTED),
    (lambda: inv.check_advisory([_violation(6)], 6, {"src": ["src/a.py"]}, lambda scope: 6),
     "a gate violation has ccn over its file's ceiling",
     f"{AT} (ccn 6, ceiling 6, claude-hook)", inv.PRINTED),
    (lambda: inv.check_advisory([], 7, {"src": ["src/a.py"]}, lambda scope: 6),
     "the advisory names its scope's ceiling", "scope src, ceiling 7", inv.PRINTED),
    (lambda: inv.check_budget(_row(ccn=14, cov=0.5), 6,
                              {"est_splits": 2, "est_uncovered_paths": 7}),
     "est_splits is ceil(ccn / target) when ccn > target",
     f"{AT} (ccn 14, cov 0.5, target 6, budget {{'est_splits': 2, 'est_uncovered_paths': 7}})",
     inv.PRINTED),
    (lambda: inv.check_dump([_mark(1 / 3)]), "a mark must be a finite number held at four decimals",
     "src/a.py\tf( x ) (mark 0.3333333333333333)", inv.MARKS_KEPT),
    (lambda: inv.check_marks_kept([_mark(8.0)], [_mark(8.0001)], adds=False), "a mark never rises",
     "src/a.py\tf( x ) (mark 8.0 -> 8.0001)", inv.MARKS_KEPT),
    (lambda: inv.check_record(_record(start=0)), "a span must satisfy 1 <= start <= end",
     inv.where(_record(start=0)), inv.STORED),
    (lambda: inv.check_rejudged(ROW_7, 6), "remedy must follow the README remedy table at ceiling 6",
     inv.where(ROW_7), inv.PRINTED),
    (lambda: inv.check_summary({**SUMMARY, "measured": 2}, 3),
     "the five flag counts sum to the functions scored (2 counted)", "run 7, functions 3",
     inv.PRINTED),
    (lambda: inv.check_summary(SUMMARY, 4), "over_target <= judged rows <= functions",
     "run 7, judged 4", inv.PRINTED),
    (lambda: inv.check_summary({**SUMMARY, "grade": "B"}, 3),
     "the grade follows the README band table (A+)", "run 7: grade B for 0 of 3 over",
     inv.PRINTED),
    (lambda: inv.check_totals(3, 4, 9.0), "0 <= over_target <= functions",
     "functions 3, over_target 4, crap_load 9.0", inv.PRINTED),
    (lambda: inv.check_totals(3, 0, 2.0),
     "crap_load is finite and at least one per function, since CRAP >= ccn >= 1",
     "functions 3, over_target 0, crap_load 2.0", inv.PRINTED),
    (lambda: inv.check_verdict(VERDICT._replace(gate_violations=[1]), 0, kept=inv.UNSETTLED),
     "verify exits 6 by the README precedence 6 > 7 > 8 > 9 > 0, and stores ok only with exit 0",
     "exit 0, ok True, gate_violations 1, unread_files 0, ratchet_regressions 0, "
     "new_failures 0, uncovered_violations 0", inv.UNSETTLED),
    (lambda: inv.check_worklist([_entry()], [], 0, {}),
     "every row over its ceiling is admitted to the worklist",
     "0 row(s) over their ceiling, 1 admitted", inv.PRINTED),
])
def test_each_stop_says_its_check_where_and_what_it_kept(call, check, at, kept):
    assert _stops(call) == inv.message(check, at, kept)


@pytest.mark.parametrize("call, text", [
    (lambda: inv._splits_problem(5, 10, 1), "est_splits is 0 when ccn <= target"),
    (lambda: inv._splits_problem(20, 10, 3), "est_splits is ceil(ccn / target) when ccn > target"),
    (lambda: inv._paths_problem(4, 0.5, 3),
     "est_uncovered_paths is (1 - cov) * ccn rounded, from 0 to ccn"),
    (lambda: inv._rise(_mark(1.0), None, False), "an update adds no mark"),
    (lambda: inv.remedy_problem(_row(remedy="bad"), None),
     "remedy must be decompose, split-lines, add-tests or ok"),
    (lambda: inv.grade_of(2, 101), "A"),
])
def test_each_problem_and_band_is_said_in_words(call, text):
    assert call() == text


# --- the bounds at their exact ends -------------------------------------------------------

def test_crap_at_either_end_of_its_band_is_inside_it():
    low = SimpleNamespace(ccn=1, crap=1 * inv._LOW, flag="measured", cov=0.5)
    high = SimpleNamespace(ccn=1, crap=(1 * 1 + 1) * inv._HIGH, flag="measured", cov=0.5)

    assert (inv._crap_problem(low), inv._crap_problem(high)) == (None, None)


def test_a_crap_a_hair_over_the_ceiling_may_read_ok_or_over():
    assert inv._expected_remedies(5, 30 * inv._HIGH, 30) == inv._OK | inv._OVER


def test_near_means_within_a_billionth_of_the_expected_value_or_of_one():
    assert [inv._near(1e-9, 0.0), inv._near(1e6 + 1e-4, 1e6), inv._near(1.5e-9, 0.0)] == [
        True, True, False]


def test_a_key_listed_twice_before_is_held_to_its_higher_mark_whatever_the_order():
    inv.check_marks_kept([_mark(5.0), _mark(3.0)], [_mark(4.0)], adds=False)


# --- each site's tally ---------------------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    """Every read of the clock is 10 ns after the one before."""
    ticks = iter(range(1_000, 10**12, 10))
    monkeypatch.setattr(inv, "perf_counter_ns", lambda: next(ticks))


def test_a_site_s_tally_counts_calls_and_adds_their_nanoseconds(monkeypatch):
    monkeypatch.setattr(inv, "perf_counter_ns", lambda: 100)
    monkeypatch.setitem(inv.COST, "edge-site", [0, 0])
    del inv.COST["edge-site"]

    inv._spent("edge-site", 60)
    inv._spent("edge-site", 60)

    assert inv.COST.pop("edge-site") == [2, 80]


@pytest.mark.parametrize("site, call, ns", [
    ("record", lambda: inv.check_record(_record()), 10),
    ("totals", lambda: inv.check_totals(3, 0, 3.0), 10),
    ("summary", lambda: inv.check_summary(SUMMARY, 3), 30),
    ("rollup", lambda: inv.check_rollup({"src": {"over_target": 0, "functions": 3,
                                                 "grade": "A+"}}), 10),
    ("marks", lambda: inv.check_marks_kept([_mark(8.0)], [_mark(8.0)], adds=False), 10),
    ("dump", lambda: inv.check_dump([_mark(8.0)]), 10),
    ("worklist", lambda: inv.check_worklist([], [], 0, {}), 10),
    ("packet", lambda: inv.check_budget(_row(), 6, {"est_splits": 0, "est_uncovered_paths": 2}),
     10),
    ("packet", lambda: inv.check_rejudged(_row(), 6), 10),
    ("advisory", lambda: inv.check_advisory([], 6, {"src": ["src/a.py"]}, lambda scope: 6), 10),
    ("hook", lambda: inv.check_violations([], {"src": ["src/a.py"]}, lambda scope: 6), 10),
    ("write_run", lambda: inv.check_rows([_row()]), 10),
    ("verdict", lambda: inv.check_verdict(VERDICT, 0, kept=inv.PRINTED), 10),
])
def test_each_check_adds_one_call_and_its_nanoseconds_to_its_own_site(clock, site, call, ns):
    """The clock reads 10 ns apart, so a check that starts and ends its own
    tally spends 10 ns, and one that runs another check between spends more."""
    before = list(inv.COST.get(site, [0, 0]))

    call()
    call()

    after = inv.COST[site]
    assert (after[0] - before[0], after[1] - before[1]) == (2, 2 * ns)


def test_the_gate_adds_its_rows_check_and_its_own_to_its_site(clock):
    """check_rows under site gate ends 10 ns after it starts; the gate's own
    tally, which started 10 ns before it, ends 10 ns after: 40 ns in all."""
    before = list(inv.COST.get("gate", [0, 0]))

    inv.check_gate([_row()], [], lambda scope: 6)

    after = inv.COST["gate"]
    assert (after[0] - before[0], after[1] - before[1]) == (2, 40)


# --- the receipt ---------------------------------------------------------------------------

def test_a_receipt_holds_each_site_that_ran_and_how_long_the_process_lived(tmp_path, monkeypatch):
    monkeypatch.setattr(inv, "COST", {"record": [2, 30], "idle": [0, 0]})
    monkeypatch.setattr(inv, "_BORN", [100])
    monkeypatch.setattr(inv, "perf_counter_ns", lambda: 1_100)

    inv._write_receipt(str(tmp_path))
    inv._write_receipt(str(tmp_path / "gone"))

    [written] = tmp_path.glob("*.json")
    assert written.name.startswith(f"{os.getpid()}-")
    assert written.read_text(encoding="utf-8") == json.dumps(
        {"alive_ns": 1000, "pid": os.getpid(), "sites": {"record": {"calls": 2, "ns": 30}}},
        sort_keys=True)


def test_a_forked_worker_starts_its_tally_and_its_clock_over(monkeypatch):
    tally = [3, 40]
    monkeypatch.setattr(inv, "COST", {"record": tally})
    monkeypatch.setattr(inv, "_BORN", [5])
    monkeypatch.setattr(inv, "perf_counter_ns", lambda: 1_100)
    monkeypatch.delenv(inv.RECEIPT_ENV, raising=False)

    inv._forked(None)

    assert (inv.COST["record"] is tally, tally, inv._BORN) == (True, [0, 0], [1_100])


def test_a_receipt_directory_arms_a_finalizer_that_writes_there(tmp_path, monkeypatch):
    armed = []
    monkeypatch.setattr(inv, "_mp_util", SimpleNamespace(
        Finalize=lambda *args, **kwargs: armed.append((args, kwargs))))
    monkeypatch.setenv(inv.RECEIPT_ENV, str(tmp_path))

    inv._watch_receipt()

    assert armed == [((None, inv._write_receipt), {"args": (str(tmp_path),), "exitpriority": 0})]
