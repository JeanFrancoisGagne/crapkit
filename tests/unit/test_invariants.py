"""The runtime checks in crapkit.invariants, one bound at a time.

Every expected value here is worked by hand from the documented definition the
check cites, never from crapkit's scoring code: CRAP = ccn^2 x (1 - cov)^3 + ccn,
so ccn 4 at cov 0.5 is 16 x 0.125 + 4 = 6.0 (README); the remedy table, the
flag table and the grade bands (README "Reading the output"); est_splits and
est_uncovered_paths (docs/agent-json.md, the next-item fields, whose README
example reads ccn 14, cov 0.5, target 6 as 3 splits and 7 paths); the exit
precedence 6 > 7 > 8 > 9 > 0 (README "Exit codes").
"""
from __future__ import annotations

import ast
import itertools
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from crapkit import invariants as inv
from crapkit.churn import FileChurn
from crapkit.errors import InternalCheckError, ToolError
from crapkit.merge import FunctionRecord
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow
from crapkit.snapshot import InventoryRow
from crapkit.verify import GateViolation, Verdict
from crapkit.worklist import WorklistEntry

SRC = Path(__file__).resolve().parents[2] / "src" / "crapkit"


def record(**over) -> FunctionRecord:
    base = dict(path="src/a.py", long_name="f( x )", start=3, end=12, ccn_std=4, ccn_mod=4,
                ccn=4, nloc=8, params=1, nesting=2, cognitive=5, occurrence=1, inline_body=0)
    return FunctionRecord(**{**base, **over})


def scored(ccn=4, cov=0.5, crap=6.0, flag="measured", remedy="ok", scope="src", **over) -> ScoredRow:
    """ccn 4 at cov 0.5 is CRAP 16 x 0.125 + 4 = 6.0, which is ok at ceiling 6."""
    base = dict(scope=scope, path="src/a.py", long_name="f( x )", start=3, end=12,
                ccn_std=ccn, ccn_mod=ccn, ccn=ccn, nloc=8, params=1, nesting=2, cov=cov,
                flag=flag, crap=crap, remedy=remedy, cognitive=5, occurrence=1, inline_body=0)
    return ScoredRow(**{**base, **over})


def stopped(call, *args, **kwargs) -> str:
    with pytest.raises(InternalCheckError) as caught:
        call(*args, **kwargs)
    return str(caught.value)


# --- the error --------------------------------------------------------------------------


def test_the_error_is_a_tool_error_with_its_own_kind_and_exit_5():
    assert issubclass(InternalCheckError, ToolError)
    assert InternalCheckError.exit_code == 5
    assert InternalCheckError.kind == "internal"


def test_the_error_crosses_a_pool_worker_pickle_intact():
    text = inv.message("a check", "src/a.py:f", inv.STORED)
    back = pickle.loads(pickle.dumps(InternalCheckError(text)))
    assert type(back) is InternalCheckError and str(back) == text


def test_the_message_names_the_check_the_row_what_was_kept_and_where_to_report():
    text = stopped(inv.check_rows, [scored(crap=3.1)], lambda scope: 6)
    lines = text.splitlines()
    assert lines[0] == "stopped: an internal check failed."
    assert lines[1] == "  check: CRAP must lie between ccn and ccn^2+ccn"
    assert lines[2].startswith("  at: src/a.py:f( x ) (ccn 4, ")
    assert "crap 3.1" in lines[2] and "cov 0.5" in lines[2]
    assert lines[3] == ("This is a crapkit bug, not a problem in your repo. No run was stored "
                        "and no ratchet mark changed. Report this message and `crapkit "
                        "--version` at https://github.com/JeanFrancoisGagne/crapkit/issues.")


# --- analyze._record --------------------------------------------------------------------


def test_a_well_formed_record_passes():
    inv.check_record(record())
    inv.check_record(record(start=7, end=7, nloc=1, ccn_std=5, ccn_mod=3, ccn=3, nesting=0,
                            cognitive=0, params=0, occurrence=0))


@pytest.mark.parametrize("wrong, check", [
    (dict(ccn_std=0, ccn_mod=0, ccn=0), "ccn_std and ccn_mod must each be at least 1"),
    (dict(ccn_mod=0, ccn=0), "ccn_std and ccn_mod must each be at least 1"),
    (dict(ccn_std=5, ccn_mod=3, ccn=5), "ccn must equal min(ccn_std, ccn_mod)"),
    (dict(start=0), "a span must satisfy 1 <= start <= end"),
    (dict(start=13, end=12), "a span must satisfy 1 <= start <= end"),
    (dict(nesting=-1), "cognitive, nesting, params and occurrence must each be at least 0"),
    (dict(cognitive=-1), "cognitive, nesting, params and occurrence must each be at least 0"),
    (dict(params=-1), "cognitive, nesting, params and occurrence must each be at least 0"),
    (dict(occurrence=-1), "cognitive, nesting, params and occurrence must each be at least 0"),
])
def test_each_record_bound_stops_its_wrong_value(wrong, check):
    text = stopped(inv.check_record, record(**wrong))
    assert f"  check: {check}\n" in text
    assert inv.STORED in text


def test_the_record_check_escapes_every_per_file_catch_all(monkeypatch, tmp_path):
    """A lizard failure refuses one file and the run goes on; a broken bound is
    no such refusal, so none of the three per-file handlers may swallow it."""
    from crapkit import analyze

    monkeypatch.setattr(analyze, "_nesting_depth", lambda rel_path, fn: -1)
    code = "def f(x):\n    return x\n"
    with pytest.raises(InternalCheckError):
        analyze.analyze_source("a.py", code)
    source = tmp_path / "a.py"
    source.write_text(code, encoding="utf-8")
    with pytest.raises(InternalCheckError):
        analyze.analyze_one((str(source), "a.py"))
    digest = analyze.hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(InternalCheckError):
        analyze._analyze_verified((str(source), "a.py", digest))


# --- store.write_run and the gate's rows -------------------------------------------------


def test_hand_worked_rows_pass_at_their_ceiling():
    rows = [
        scored(),  # ccn 4, cov 0.5: 6.0, ok at 6
        scored(cov=1.0, crap=4.0),  # full coverage: CRAP is ccn
        scored(cov=0.0, crap=20.0, flag="untested", remedy="add-tests"),  # 16 + 4
        scored(cov=0.0, crap=20.0, flag="no-lane", remedy="split-lines"),
        scored(ccn=7, cov=1.0, crap=7.0, remedy="decompose"),  # ccn over 6
        scored(ccn=7, cov=0.0, crap=7.0, flag="cc-only", remedy="decompose"),  # cc-only: CRAP = ccn
        scored(ccn=5, cov=0.0, crap=5.0, flag="cc-only", remedy="ok"),
        scored(ccn=3, cov=0.25, crap=3 * 3 * 0.421875 + 3, remedy="add-tests"),  # 6.796875 > 6
    ]
    inv.check_rows(rows, lambda scope: 6)


def test_inventory_rows_take_the_shape_checks_only():
    ok = InventoryRow("src", "src/a.py", "f( )", 1, 4, 2, 2, 2, 3, 0, 1, 0, 1)
    inv.check_rows([ok])
    bad = ok._replace(start=5)
    assert "1 <= start <= end" in stopped(inv.check_rows, [bad])


@pytest.mark.parametrize("wrong, check", [
    (dict(crap=3.9), "CRAP must lie between ccn and ccn^2+ccn"),
    (dict(crap=20.5), "CRAP must lie between ccn and ccn^2+ccn"),
    (dict(crap=float("nan")), "CRAP must lie between ccn and ccn^2+ccn"),
    (dict(crap=float("inf")), "CRAP must lie between ccn and ccn^2+ccn"),
    (dict(cov=float("nan")), "coverage must be a number from 0 to 1"),
    (dict(cov=-0.01), "coverage must be a number from 0 to 1"),
    (dict(cov=1.01), "coverage must be a number from 0 to 1"),
    (dict(cov=1.0, crap=6.0), "at coverage 1, CRAP must equal ccn"),
    (dict(cov=0.0, crap=6.0), "at coverage 0, CRAP must equal ccn^2+ccn"),
    (dict(flag="partial"), "flag must be measured, untested, no-lane or cc-only"),
    (dict(flag="untested"), "an untested or no-lane row must read coverage 0"),
    (dict(flag="no-lane"), "an untested or no-lane row must read coverage 0"),
    (dict(flag="cc-only", cov=0.0, crap=20.0), "a cc-only row's CRAP must equal its ccn"),
    (dict(remedy="add-tests"), "remedy must follow the README remedy table at ceiling 6"),
    (dict(remedy="decompose"), "remedy must follow the README remedy table at ceiling 6"),
])
def test_each_scored_row_bound_stops_its_wrong_value(wrong, check):
    text = stopped(inv.check_rows, [scored(**wrong)], lambda scope: 6)
    assert f"  check: {check}\n" in text


@pytest.mark.parametrize("row, ceiling", [
    (scored(ccn=7, cov=1.0, crap=7.0, remedy="ok"), 6),  # ccn 7 > 6: only decompose
    (scored(ccn=4, cov=0.0, crap=20.0, flag="untested", remedy="ok"), 6),  # 20 > 6
    (scored(ccn=4, cov=0.0, crap=20.0, flag="untested", remedy="decompose"), 6),
    (scored(ccn=4, cov=0.0, crap=20.0, flag="untested", remedy="add-tests"), 20),  # 20 <= 20
])
def test_the_remedy_follows_the_readme_table_against_the_row_s_own_scope_ceiling(row, ceiling):
    ceilings = {"src": ceiling}
    assert "README remedy table" in stopped(inv.check_rows, [row], ceilings.__getitem__)


def test_the_remedy_rule_reads_each_row_s_scope_ceiling():
    """ccn 7 is decompose under a ceiling of 6 and ok under a scope ceiling of 10."""
    rows = [scored(ccn=7, cov=1.0, crap=7.0, remedy="decompose", scope="src"),
            scored(ccn=7, cov=1.0, crap=7.0, remedy="ok", scope="util")]
    inv.check_rows(rows, {"src": 6, "util": 10}.__getitem__)


def test_without_a_ceiling_the_remedy_must_still_be_a_documented_word():
    inv.check_rows([scored(remedy="add-tests")])
    assert "remedy must be decompose" in stopped(inv.check_rows, [scored(remedy="fix-it")])


# --- ratchet marks -----------------------------------------------------------------------


def mark(value: float, name: str = "f( x )") -> RatchetEntry:
    return RatchetEntry("src/a.py", name, value)


def test_a_mark_that_falls_or_stays_passes_and_a_rise_stops():
    inv.check_marks_kept([mark(8.0)], [mark(7.5)], adds=False)
    inv.check_marks_kept([mark(8.0)], [mark(8.0)], adds=False)
    inv.check_marks_kept([mark(8.0)], [], adds=False)  # a dropped mark
    text = stopped(inv.check_marks_kept, [mark(8.0)], [mark(8.0001)], adds=False)
    assert "  check: a mark never rises\n" in text and inv.MARKS_KEPT in text


def test_an_update_adds_no_mark_and_a_seed_may():
    inv.check_marks_kept([mark(8.0)], [mark(8.0), mark(9.0, "g( )")], adds=True)
    text = stopped(inv.check_marks_kept, [mark(8.0)], [mark(8.0), mark(9.0, "g( )")], adds=False)
    assert "an update adds no mark" in text


def test_a_key_listed_twice_is_held_to_its_higher_mark():
    inv.check_marks_kept([mark(5.0), mark(8.0)], [mark(5.0), mark(8.0)], adds=False)


@pytest.mark.parametrize("value", [8.0, 66.0714, 1.0001, 12345.6789])
def test_a_four_decimal_mark_dumps(value):
    inv.check_dump([mark(value)])


@pytest.mark.parametrize("value", [8.00001, 0.0, -2.0, float("inf"), float("nan"), 1 / 3])
def test_a_mark_the_file_cannot_hold_stops_the_dump(value):
    text = stopped(inv.check_dump, [mark(value)])
    assert "held at four decimals" in text and inv.MARKS_KEPT in text


def test_an_unstamped_file_still_dumps():
    """docs/ratchet.md "The metric stamp": prune, move and the merge driver keep
    the recorded stamp, and a file written before stamping has none."""
    from crapkit.ratchet import dump_ratchet

    assert dump_ratchet([mark(8.0)], stamp="") == "path\tlong_name\tcrap\nsrc/a.py\tf( x )\t8.0000\n"


# --- the worklist ------------------------------------------------------------------------


def entry(path="src/a.py", commits=2, authors=1, weight=0.6, risk=4.2, remedy="decompose",
          ccn=7) -> WorklistEntry:
    return WorklistEntry("src", path, "f( x )", 3, 12, ccn, ccn, 8, commits, authors, weight,
                         risk, "measured", remedy, 7.0, 1.0)


def churn(*weights_by_commits) -> dict:
    return {f"f{i}.py": FileChurn(commits, 1, weight)
            for i, (commits, weight) in enumerate(weights_by_commits)}


def test_a_worklist_inside_its_bounds_passes():
    active = [entry(risk=4.2), entry(path="src/b.py", risk=1.5, remedy="ok", commits=3, weight=0.3)]
    dormant = [entry(path="src/c.py", commits=0, authors=0, weight=0.0, risk=0.0)]
    inv.check_worklist(active, dormant, 2, churn((2, 0.6), (3, 0.3)))


def test_a_row_over_its_ceiling_that_was_not_admitted_stops_the_list():
    text = stopped(inv.check_worklist, [entry()], [], 2, churn((2, 0.6)))
    assert "every row over its ceiling is admitted" in text
    assert "2 row(s) over their ceiling, 1 admitted" in text and inv.PRINTED in text


@pytest.mark.parametrize("active, dormant, check", [
    ([entry(commits=0, authors=0, weight=0.0)], [], "active rows have commits"),
    ([], [entry(commits=1, weight=0.2)], "active rows have commits"),
    ([entry(authors=3)], [], "0 <= authors <= commits"),
    ([entry(weight=2.5)], [], "0 <= weight <= commits"),
    ([entry(weight=-0.1)], [], "0 <= weight <= commits"),
])
def test_each_churn_bound_stops_its_wrong_value(active, dormant, check):
    over = len(active) + len(dormant)
    assert check in stopped(inv.check_worklist, active, dormant, over, churn((2, 0.6)))


def test_a_commit_weighs_at_most_one_half_unless_every_commit_shares_one_timestamp():
    """README "Risk": the logistic rises to 0.5 for the newest commit; a log
    whose commits share one timestamp counts each commit once, so every weight
    is whole."""
    heavy = [entry(commits=2, weight=1.5)]
    inv.check_worklist(heavy, [], 1, churn((2, 2.0), (1, 1.0)))
    text = stopped(inv.check_worklist, heavy, [], 1, churn((2, 1.5), (3, 0.4)))
    assert "a commit weighs at most 0.5" in text


def test_the_active_list_ranks_by_risk_highest_first():
    ranked = [entry(risk=1.5), entry(path="src/b.py", risk=4.2)]
    assert "ranks by risk, highest first" in stopped(inv.check_worklist, ranked, [], 2,
                                                     churn((2, 0.6)))


# --- totals, grade and summary -----------------------------------------------------------


@pytest.mark.parametrize("over, functions, letter", [
    (0, 0, "A+"), (0, 100, "A+"), (1, 100, "A"), (2, 100, "B"), (4, 100, "B"),
    (5, 100, "C"), (9, 100, "C"), (10, 100, "D"), (19, 100, "D"), (20, 100, "F"),
    (100, 100, "F"), (1, 49, "B"), (1, 50, "B"), (1, 51, "A"),
])
def test_the_grade_bands_are_the_readme_table(over, functions, letter):
    """A under 2%, B under 5%, C under 10%, D under 20%, F at 20% or more. 1 of
    50 is exactly 2%, so B; 1 of 51 is 1.96%, so A; 1 of 49 is 2.04%, so B."""
    assert inv.grade_of(over, functions) == letter
    inv.check_grade(over, functions, letter, "scope src")


def test_a_wrong_grade_stops_the_rollup():
    text = stopped(inv.check_rollup, {"src": {"functions": 100, "over_target": 2,
                                              "grade": "A", "crap_load": 300.0}})
    assert "the grade follows the README band table (B)" in text
    assert "scope src: grade A for 2 of 100 over" in text


@pytest.mark.parametrize("functions, over, load, check", [
    (10, 11, 40.0, "0 <= over_target <= functions"),
    (10, -1, 40.0, "0 <= over_target <= functions"),
    (10, 2, 9.5, "crap_load is finite and at least one per function"),
    (10, 2, float("nan"), "crap_load is finite and at least one per function"),
    (10, 2, float("inf"), "crap_load is finite and at least one per function"),
])
def test_each_totals_bound_stops_its_wrong_sum(functions, over, load, check):
    assert check in stopped(inv.check_totals, functions, over, load)


def test_totals_inside_their_bounds_pass():
    inv.check_totals(0, 0, 0.0)
    inv.check_totals(10, 10, 10.0)


def summary(**over) -> dict:
    base = {"run_id": 1, "functions": 10, "measured": 6, "untested": 2, "no_lane": 1,
            "cc_only": 1, "over_target": 1, "grade": "D", "crap_load": 40.0}
    return {**base, **over}


def test_a_summary_inside_its_bounds_passes():
    inv.check_summary(summary(), judged=10)  # 1 of 10 is 10%: not under 10%, so D


def test_each_summary_bound_stops_its_wrong_count():
    assert "four flag counts sum" in stopped(inv.check_summary, summary(measured=5), 10)
    assert "over_target <= judged rows" in stopped(inv.check_summary, summary(), 0)
    assert "README band table (D)" in stopped(inv.check_summary, summary(grade="C"), 10)


# --- the packet --------------------------------------------------------------------------


@pytest.mark.parametrize("ccn, cov, target, splits, paths", [
    (14, 0.5, 6, 3, 7),  # the README next-item example
    (6, 1.0, 6, 0, 0),
    (7, 0.0, 6, 2, 7),
    (12, 0.25, 6, 2, 9),
    (13, 0.0, 6, 3, 13),
    (1, 0.0, 1, 0, 1),
])
def test_hand_worked_budgets_pass(ccn, cov, target, splits, paths):
    inv.check_budget(scored(ccn=ccn, cov=cov), target,
                     {"est_splits": splits, "est_uncovered_paths": paths})


@pytest.mark.parametrize("ccn, cov, target, splits, paths, check", [
    (6, 1.0, 6, 1, 0, "est_splits is 0 when ccn <= target"),
    (14, 0.5, 6, 2, 7, "est_splits is ceil(ccn / target)"),
    (14, 0.5, 6, 4, 7, "est_splits is ceil(ccn / target)"),
    (14, 0.5, 6, 3, 9, "est_uncovered_paths is (1 - cov) * ccn rounded"),
    (14, 0.5, 6, 3, -1, "est_uncovered_paths is (1 - cov) * ccn rounded"),
    (4, 0.0, 6, 0, 5, "est_uncovered_paths is (1 - cov) * ccn rounded"),
])
def test_each_budget_bound_stops_its_wrong_estimate(ccn, cov, target, splits, paths, check):
    text = stopped(inv.check_budget, scored(ccn=ccn, cov=cov), target,
                   {"est_splits": splits, "est_uncovered_paths": paths})
    assert check in text and inv.PRINTED in text


def test_a_rejudged_row_follows_today_s_ceiling():
    inv.check_rejudged(scored(ccn=5, cov=1.0, crap=5.0, remedy="decompose"), 4)
    text = stopped(inv.check_rejudged, scored(ccn=5, cov=1.0, crap=5.0, remedy="ok"), 4)
    assert "README remedy table at ceiling 4" in text and inv.PRINTED in text


# --- gates -------------------------------------------------------------------------------


class Violation(NamedTuple):
    path: str
    long_name: str
    ccn: int


def test_a_hook_violation_is_over_its_file_s_scope_ceiling():
    in_scope = {"src": ["src/a.py"], "util": ["util/b.py"]}
    ceilings = {"src": 6, "util": 10}.__getitem__
    inv.check_violations([Violation("src/a.py", "f( )", 7), Violation("util/b.py", "g( )", 11)],
                         in_scope, ceilings)
    text = stopped(inv.check_violations, [Violation("util/b.py", "g( )", 7)], in_scope, ceilings)
    assert "ccn over its file's ceiling" in text and "ceiling 10" in text


def test_the_advisory_names_its_scope_s_ceiling_and_only_breaches_over_it():
    in_scope = {"src": ["src/a.py"], "util": []}
    inv.check_advisory([Violation("src/a.py", "f( )", 7)], 6, in_scope, {"src": 6}.__getitem__)
    assert "names its scope's ceiling" in stopped(
        inv.check_advisory, [], 8, in_scope, {"src": 6}.__getitem__)
    assert "ccn over its file's ceiling" in stopped(
        inv.check_advisory, [Violation("src/a.py", "f( )", 6)], 6, in_scope, {"src": 6}.__getitem__)


def test_the_gate_checks_its_rows_and_its_breaches():
    over = scored(ccn=7, cov=1.0, crap=7.0, remedy="decompose")
    breach = GateViolation(over.path, over.long_name, over.start, 7, 1.0, 7.0, "decompose")
    inv.check_gate([over], [breach], lambda scope: 6)
    assert "README remedy table" in stopped(inv.check_gate, [over._replace(remedy="ok")], [],
                                            lambda scope: 6)
    at_seven = over._replace(remedy="ok")  # ccn 7 and CRAP 7 are ok at a ceiling of 7
    assert "ccn over its file's ceiling" in stopped(inv.check_gate, [at_seven], [breach],
                                                    lambda scope: 7)


# --- the verify verdict ------------------------------------------------------------------

# README "Exit codes": the first of 6, 7, 8, 9 that fires, in that order; else 0.
_EXIT_OF = {(g, r, n, u): (6 if g else 7 if r else 8 if n else 9 if u else 0)
            for g, r, n, u in itertools.product((False, True), repeat=4)}


def verdict(g: bool, r: bool, n: bool, u: bool, ok: bool) -> Verdict:
    return Verdict(ok=ok, gate_violations=["g"] if g else [],
                   ratchet_regressions=["r"] if r else [], new_failures=["n"] if n else [],
                   dirty_failures=[], uncovered_violations=("u",) if u else ())


@pytest.mark.parametrize("findings", sorted(_EXIT_OF))
def test_all_sixteen_finding_sets_pass_at_the_readme_exit(findings):
    code = _EXIT_OF[findings]
    inv.check_verdict(verdict(*findings, ok=code == 0), code, kept=inv.STORED)
    assert inv.verdict_exit(verdict(*findings, ok=code == 0)) == code


@pytest.mark.parametrize("findings", sorted(_EXIT_OF))
def test_a_wrong_exit_or_ok_stops_every_finding_set(findings):
    code = _EXIT_OF[findings]
    wrong = 0 if code else 6
    assert "README precedence" in stopped(inv.check_verdict, verdict(*findings, ok=code == 0),
                                          wrong, kept=inv.STORED)
    text = stopped(inv.check_verdict, verdict(*findings, ok=code != 0), code, kept=inv.UNSETTLED)
    assert inv.UNSETTLED in text


# --- structure ---------------------------------------------------------------------------


def _module_names(node) -> list[str]:
    """`from .errors import X` reads `.errors`; `import crapkit.score` reads itself."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return ["." * node.level + str(node.module)]


def _imports(tree: ast.Module) -> list:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]


def _crapkit_imports(path: Path) -> set[str]:
    """The crapkit modules a module imports, relative or absolute, by short name."""
    names = itertools.chain.from_iterable(map(_module_names, _imports(ast.parse(path.read_bytes()))))
    return {name.removeprefix("crapkit.").lstrip(".") for name in names
            if name.startswith((".", "crapkit"))}


def test_the_checks_import_nothing_that_calculates_what_they_guard():
    """A bound computed by the code it guards agrees with that code's bugs."""
    imported = _crapkit_imports(SRC / "invariants.py")
    assert imported == {"errors"}
    assert not imported & {"score", "worklist", "digest", "verify", "churn"}


def _subscript_key(node) -> str | None:
    """KEY of `os.environ[KEY]`."""
    if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ":
        return ast.unparse(node.slice)
    return None


def _get_key(node) -> str | None:
    """KEY of `os.environ.get(KEY)`."""
    if isinstance(node, ast.Call) and ast.unparse(node.func) == "os.environ.get":
        return ast.unparse(node.args[0])
    return None


def _environment_keys(tree: ast.Module) -> list[str]:
    return [key for node in ast.walk(tree) for key in (_subscript_key(node), _get_key(node)) if key]


def _parameter_names(tree: ast.Module) -> set[str]:
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    return {a.arg for fn in functions for a in fn.args.args + fn.args.kwonlyargs}


def test_no_variable_or_flag_turns_the_checks_off():
    """The one environment variable the module reads names a receipt file, and
    no check takes an argument that skips it."""
    tree = ast.parse((SRC / "invariants.py").read_bytes())
    assert set(_environment_keys(tree)) == {"RECEIPT_ENV"}
    switches = [name for name in _parameter_names(tree)
                if any(word in name for word in ("skip", "enable", "disable", "off"))]
    assert switches == []


# Each call site calls its check unconditionally: not under an if, a try or a loop.
_SITES = [
    ("analyze.py", "_record", "check_record"),
    ("cli/scoring.py", "cmd_inventory", "check_rows"),
    ("cli/scoring.py", "cmd_coverage", "check_rows"),
    ("cli/verifying.py", "cmd_verify", "check_rows"),
    ("ratchet.py", "seed_ratchet", "check_marks_kept"),
    ("ratchet.py", "update_ratchet", "check_marks_kept"),
    ("ratchet.py", "dump_ratchet", "check_dump"),
    ("worklist.py", "build_worklist", "check_worklist"),
    ("digest.py", "totals_from_counts", "check_totals"),
    ("digest.py", "scope_rollup", "check_rollup"),
    ("packet.py", "rejudged", "check_rejudged"),
    ("packet.py", "budget", "check_budget"),
    ("hook.py", "_touched_over_ceiling", "check_violations"),
    ("cli/verifying.py", "cmd_verify", "check_verdict"),
    ("cli/verifying.py", "_settle_verify", "check_verdict"),
    ("cli/scoring.py", "_gate_verdict", "check_gate"),
    ("cli/scoring.py", "_coverage_summary", "check_summary"),
    ("cli/claude_hook.py", "_judge", "_check_advisory"),
    ("cli/claude_hook.py", "_check_advisory", "check_advisory"),
]


def _function(tree: ast.Module, qualname: str):
    scope = tree
    for part in qualname.split("."):
        scope = next(node for node in scope.body
                     if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == part)
    return scope


_BRANCHING = (ast.If, ast.Try, ast.For, ast.While, ast.With, ast.Match)


def _unconditional_calls(fn) -> set[str]:
    """Names called by the statements of `fn`'s own body that branch nowhere."""
    return {node.func.id for node in _unconditional_nodes(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


@pytest.mark.parametrize("module, function, check", _SITES)
def test_every_site_calls_its_check_on_every_path(module, function, check):
    tree = ast.parse((SRC / module).read_bytes())
    assert check in _unconditional_calls(_function(tree, function))


def _write_run_calls(fn) -> list[ast.Call]:
    return [node for node in ast.walk(fn) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "write_run"]


def _rows_written(call: ast.Call) -> str:
    return next(ast.unparse(k.value) for k in call.keywords if k.arg == "rows")


def _unconditional_nodes(fn) -> list:
    """Every node under the statements of fn's own body that branch nowhere."""
    statements = [statement for statement in fn.body if not isinstance(statement, _BRANCHING)]
    return [node for statement in statements for node in ast.walk(statement)]


def _checked_rows(fn) -> set[str]:
    """The first argument of every check_rows call on fn's unconditional path."""
    calls = [node for node in _unconditional_nodes(fn)
             if isinstance(node, ast.Call) and ast.unparse(node.func) == "check_rows"]
    return {ast.unparse(call.args[0]) for call in calls}


def _functions(path: Path) -> list:
    return [node for node in ast.walk(ast.parse(path.read_bytes()))
            if isinstance(node, ast.FunctionDef)]


def _unchecked_in(fn, module: str) -> list[str]:
    checked = _checked_rows(fn)
    return [f"{module}:{fn.name} writes {rows}" for rows in map(_rows_written, _write_run_calls(fn))
            if rows != "[]" and rows not in checked]


def _unchecked_writes(path: Path) -> list[str]:
    """`module:function writes rows` for every write_run whose rows no check read first."""
    return [problem for fn in _functions(path) for problem in _unchecked_in(fn, path.name)]


def _writer_modules() -> list[Path]:
    return [path for path in SRC.rglob("*.py") if path.name != "store.py"]


def test_every_run_crapkit_stores_passes_the_row_check_first():
    """The store is a storage API that tests fill with made-up rows, so the row
    check runs where production writes: in the command, on the rows it hands
    SnapshotStore.write_run, on every path. A run with no rows (the hook's
    override record) has nothing to check."""
    writers = _writer_modules()
    assert list(itertools.chain.from_iterable(map(_unchecked_writes, writers))) == []
    assert sum(len(_write_run_calls(ast.parse(path.read_bytes()))) for path in writers) == 4


def _receipts(directory: Path) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.iterdir())]


def _run_with_receipts(code: str, directory: Path) -> list[dict]:
    env = {**os.environ, inv.RECEIPT_ENV: str(directory)}
    subprocess.run([sys.executable, "-c", code], env=env, check=True, timeout=120)
    return _receipts(directory)


def test_the_receipt_holds_each_site_s_calls_and_nanoseconds(tmp_path):
    code = ("from crapkit import invariants as inv\n"
            "from crapkit.merge import FunctionRecord\n"
            "inv.check_record(FunctionRecord('a.py', 'f( )', 1, 2, 1, 1, 1, 2, 0, 0))\n"
            "inv.check_totals(3, 1, 9.0)\n")
    (tally,) = _run_with_receipts(code, tmp_path)
    assert set(tally["sites"]) == {"record", "totals"}
    assert tally["sites"]["record"]["calls"] == 1 and tally["sites"]["record"]["ns"] > 0
    spent = sum(site["ns"] for site in tally["sites"].values())
    assert tally["alive_ns"] > spent, "the process ran longer than its checks did"


def test_a_pool_worker_writes_its_own_tally_and_repeats_none_of_the_command_s(tmp_path):
    """The analysis pool runs the record check in workers: spawned on Windows
    and macOS, forked on Linux. Each worker reports what it checked, once,
    including the packet site, whose tally list lives for the whole process."""
    code = ("from concurrent.futures import ProcessPoolExecutor\n"
            "from crapkit import invariants as inv\n"
            "from crapkit.score import ScoredRow\n"
            "row = ScoredRow('src', 'a.py', 'f( )', 1, 2, 4, 4, 4, 2, 0, 0, 0.5, 'measured',"
            " 6.0, 'ok', 0, 1, 0)\n"
            "inv.check_totals(3, 1, 9.0)\n"
            "inv.check_rejudged(row, 6)\n"
            "with ProcessPoolExecutor(1) as pool:\n"
            "    pool.submit(inv.check_totals, 2, 0, 4.0).result()\n"
            "    pool.submit(inv.check_rejudged, row, 6).result()\n")
    tallies = _run_with_receipts(code, tmp_path)
    assert len({tally["pid"] for tally in tallies}) == 2
    calls = [{site: tally["sites"][site]["calls"] for site in ("totals", "packet")}
             for tally in tallies]
    assert calls == [{"totals": 1, "packet": 1}] * 2
