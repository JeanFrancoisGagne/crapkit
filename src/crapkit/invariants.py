"""Bounds every number crapkit calculates must meet before it is stored or printed.

Each check is written from the documented definition of the number it guards,
never by calling the code that computes it: a check that recomputed CRAP
through `score.crap` would agree with a broken `score.crap`. So this module
imports nothing from score, worklist, digest, verify or churn. The sources:

- CRAP = ccn^2 x (1 - cov)^3 + ccn (README), so ccn <= CRAP <= ccn^2 + ccn,
  CRAP is ccn at full coverage and ccn^2 + ccn at none.
- The flag and remedy tables and the grade bands (README "Reading the output").
- `ccn` is min(ccn_std, ccn_mod); `est_splits` is 0 at or under the target,
  else ceil(ccn / target); `est_uncovered_paths` is round((1 - cov) * ccn)
  (docs/agent-json.md, next-item fields).
- A mark never rises and an update adds none; marks are CRAP to four decimals
  (docs/ratchet.md).
- Churn weighs each commit at most 0.5, the newest; a log whose commits share
  one timestamp counts each commit once (README "Risk").
- `verify` exits on the first of 6, 7, 8, 9 that fires, else 0 (README "Exit codes").

A number outside its bound raises InternalCheckError: exit 5, kind `internal`
under --json and on MCP. The message names the check, the function or count it
caught, and what the stop kept from happening. No flag or variable turns the
checks off.

Each check adds its calls and nanoseconds to COST under its site's name. When
CRAPKIT_INVARIANT_RECEIPT names a directory, each process, the command and
every analysis-pool worker alike, writes its own tally there as one JSON file
when it exits: each site's calls and nanoseconds, and how long the process
ran, so a harness can tell what share of a run's time the checks took.
"""
from __future__ import annotations

from itertools import chain
import json
import math
from multiprocessing import util as _mp_util
import os
import sys
import tempfile
from time import perf_counter_ns
from typing import NoReturn

from .errors import InternalCheckError

ISSUES = "https://github.com/JeanFrancoisGagne/crapkit/issues"
RECEIPT_ENV = "CRAPKIT_INVARIANT_RECEIPT"

# What the stop kept from happening, said by the site that stopped.
STORED = "No run was stored and no ratchet mark changed."
MARKS_KEPT = "The marks file was not rewritten."
UNSETTLED = ("The run was stored without a verdict, so it cannot serve as a baseline, "
             "and the marks file was not tightened.")
PRINTED = "Nothing was printed as a verdict."

FLAGS = frozenset(("measured", "untested", "excluded", "no-lane", "cc-only"))
REMEDIES = frozenset(("decompose", "split-lines", "add-tests", "ok"))
_ZERO_COV_FLAGS = frozenset(("untested", "excluded", "no-lane"))
# README's Flags table: no test can move these rows' number, so CRAP is ccn.
_CCN_FLAGS = frozenset(("cc-only", "excluded"))
_DECOMPOSE = frozenset(("decompose",))
_OK = frozenset(("ok",))
_OVER = frozenset(("add-tests", "split-lines"))
_SCORED_FIELDS = 16  # a scored row carries cov, flag, crap and remedy; an inventory row does not
_REL = 1e-9
_LOW, _HIGH = 1.0 - _REL, 1.0 + _REL
# README "Grade and CRAP load": A under 2%, B under 5%, C under 10%, D under 20%.
_BANDS = ((2, "A"), (5, "B"), (10, "C"), (20, "D"))
# README "Exit codes": verify reports the first of these that fires.
_PRECEDENCE = ((6, "gate_violations"), (6, "unread_files"), (7, "ratchet_regressions"),
               (8, "new_failures"), (9, "uncovered_violations"))

COST: dict[str, list[int]] = {}
_BORN = [perf_counter_ns()]  # when this process's tally began


def message(check: str, at: str, kept: str) -> str:
    """The refusal text. The CLI prints it after its `crapkit: ` prefix."""
    return (f"stopped: an internal check failed.\n"
            f"  check: {check}\n"
            f"  at: {at}\n"
            f"This is a crapkit bug, not a problem in your repo. {kept} "
            f"Report this message and `crapkit --version` at {ISSUES}.")


def _stop(check: str, at: str, kept: str) -> NoReturn:
    raise InternalCheckError(message(check, at, kept))


def _spent(site: str, began: int) -> None:
    """Add one call to the site's tally. The per-row sites call it once a
    function, so it allocates nothing after a site's first call."""
    tally = COST.get(site)
    if tally is None:
        tally = COST[site] = [0, 0]
    tally[0] += 1
    tally[1] += perf_counter_ns() - began


def _write_receipt(directory: str) -> None:
    """This process's tally, in a file no other process writes: processes that
    exit together would interleave appends to one shared file."""
    sites = {site: {"calls": calls, "ns": ns} for site, (calls, ns) in sorted(COST.items())
             if calls}
    tally = {"pid": os.getpid(), "alive_ns": perf_counter_ns() - _BORN[0], "sites": sites}
    try:
        handle, _ = tempfile.mkstemp(suffix=".json", prefix=f"{os.getpid()}-", dir=directory)
        with open(handle, "w", encoding="utf-8") as receipt:
            receipt.write(json.dumps(tally, sort_keys=True))
    except OSError:
        pass  # a receipt is a measurement aid; it never changes a verdict


def _watch_receipt() -> None:
    """Write the tally at exit. A multiprocessing finalizer runs both at the
    command's exit and at a pool worker's, which skips atexit."""
    directory = os.environ.get(RECEIPT_ENV)
    if directory:
        _mp_util.Finalize(None, _write_receipt, args=(directory,), exitpriority=0)


def _forked(_module) -> None:
    """A forked worker starts from a copy of the command's tally, which the
    command reports itself. The lists stay, since a site may hold one."""
    for tally in COST.values():
        tally[:] = [0, 0]
    _BORN[0] = perf_counter_ns()
    _watch_receipt()


_watch_receipt()
# A worker multiprocessing forks (the fork and forkserver start methods) drops
# the finalizers it inherited, then runs these hooks: the worker's own goes here.
_mp_util.register_after_fork(sys.modules[__name__], _forked)


# --- one function's measured shape ----------------------------------------------------


def _ccn_problem(r) -> str | None:
    if r.ccn_std < 1 or r.ccn_mod < 1:
        return "ccn_std and ccn_mod must each be at least 1"
    if r.ccn != min(r.ccn_std, r.ccn_mod):
        return "ccn must equal min(ccn_std, ccn_mod)"
    return None


def _span_problem(r) -> str | None:
    """nloc also lies in 0 to the span's line count, but two readers break that
    today, and a stop there would refuse every run of a repo holding one such
    function. The TypeScript expression-arrow reader ends an arrow whose body
    starts on the next line at the arrow's own line while nloc counts the body
    (calc-bug runtime-guards-1). The Python reader subtracts each line of a
    triple-quoted f-string that follows an interpolation, as if the text were a
    comment string, so nloc falls below 0 (calc-bug runtime-guards-3). Both are
    pinned by tests/accuracy/runtime_guards/test_invariant_verdict.py, and each
    bound joins this check when its reader is fixed."""
    if not 1 <= r.start <= r.end:
        return "a span must satisfy 1 <= start <= end"
    return None


def _count_problem(r) -> str | None:
    if min(r.cognitive, r.nesting, r.params, r.occurrence) < 0:
        return "cognitive, nesting, params and occurrence must each be at least 0"
    return None


def record_problem(r) -> str | None:
    """The first bound a measured function breaks, or None."""
    return _ccn_problem(r) or _span_problem(r) or _count_problem(r)


def where(r) -> str:
    """The row a check caught, with the numbers the check read."""
    numbers = [f"ccn {r.ccn}", f"lines {r.start}-{r.end}", f"nloc {r.nloc}"]
    numbers += [f"{name} {getattr(r, name)!r}" for name in ("cov", "crap", "flag", "remedy")
                if hasattr(r, name)]
    return f"{r.path}:{r.long_name} ({', '.join(numbers)})"


def check_record(record) -> None:
    """analyze._record: the numbers one function was read at."""
    began = perf_counter_ns()
    problem = record_problem(record)
    if problem:
        _stop(problem, where(record), STORED)
    _spent("record", began)


# --- one scored row --------------------------------------------------------------------


def _near(value: float, expected: float) -> bool:
    return abs(value - expected) <= _REL * max(abs(expected), 1.0)


def _cov_problem(row) -> str | None:
    if 0.0 <= row.cov <= 1.0:  # False for NaN
        return None
    return "coverage must be a number from 0 to 1"


# Coverage values at which the formula pins CRAP exactly: (CRAP of ccn, the rule).
_EXACT_AT = {1.0: (lambda ccn: ccn, "at coverage 1, CRAP must equal ccn"),
             0.0: (lambda ccn: ccn * ccn + ccn, "at coverage 0, CRAP must equal ccn^2+ccn")}


def _ends_problem(row) -> str | None:
    if row.flag in _CCN_FLAGS:
        return None if _near(row.crap, row.ccn) else "a cc-only or excluded row's CRAP must equal its ccn"
    exact = _EXACT_AT.get(row.cov)
    if exact is None or _near(row.crap, exact[0](row.ccn)):
        return None
    return exact[1]


def _crap_problem(row) -> str | None:
    ccn = row.ccn
    if not ccn * _LOW <= row.crap <= (ccn * ccn + ccn) * _HIGH:  # False for NaN
        return "CRAP must lie between ccn and ccn^2+ccn"
    return _ends_problem(row)


def _flag_problem(row) -> str | None:
    if row.flag not in FLAGS:
        return "flag must be measured, untested, excluded, no-lane or cc-only"
    if row.flag in _ZERO_COV_FLAGS and row.cov != 0.0:
        return "an untested, excluded or no-lane row must read coverage 0"
    return None


def _expected_remedies(ccn: int, crap: float, ceiling: int) -> frozenset:
    """README's remedy table. split-lines and add-tests share a condition and
    differ on whether another function shares the lines, which a row cannot say.
    A CRAP at its ceiling reads ok, and a double a hair above the ceiling
    cannot say whether the exact CRAP sits at it or over it: CRAP(18, 2/3) is
    exactly 30 and its double 30.000000000000004."""
    if ccn > ceiling:
        return _DECOMPOSE
    if crap <= ceiling:
        return _OK
    return _OVER if crap > ceiling * _HIGH else _OK | _OVER


def remedy_problem(row, ceiling: int | None) -> str | None:
    if ceiling is None:
        return None if row.remedy in REMEDIES else "remedy must be decompose, split-lines, add-tests or ok"
    if row.remedy in _expected_remedies(row.ccn, row.crap, ceiling):
        return None
    return f"remedy must follow the README remedy table at ceiling {ceiling}"


def scored_problem(row, ceiling: int | None) -> str | None:
    """The first bound a scored row breaks, or None. `ceiling` None skips only
    the remedy-against-ceiling rule."""
    return (_cov_problem(row) or _crap_problem(row) or _flag_problem(row)
            or remedy_problem(row, ceiling))


def _row_problem(row, ceiling_of) -> str | None:
    problem = record_problem(row)
    if problem or len(row) < _SCORED_FIELDS:
        return problem
    return scored_problem(row, None if ceiling_of is None else ceiling_of(row.scope))


def check_rows(rows, ceiling_of=None, *, kept: str = STORED, site: str = "write_run") -> None:
    """The rows a command is about to store (inventory, coverage and verify
    call it right before SnapshotStore.write_run) or to judge (rescore's gate):
    every row's shape, and every scored row's coverage, CRAP, flag and remedy.
    `ceiling_of(scope)` is the parsed config's ceiling rule."""
    began = perf_counter_ns()
    for row in rows:
        problem = _row_problem(row, ceiling_of)
        if problem:
            _stop(problem, where(row), kept)
    _spent(site, began)


# --- ratchet marks ---------------------------------------------------------------------


def _highest(prior) -> dict:
    """Each key's mark before the write; the higher one when a file lists a key twice."""
    marks: dict = {}
    for e in prior:
        key = (e.path, e.long_name)
        marks[key] = max(e.crap, marks.get(key, e.crap))
    return marks


def _rise(entry, before, adds: bool) -> str | None:
    if before is None:
        return None if adds else "an update adds no mark"
    return None if entry.crap <= before else "a mark never rises"


def check_marks_kept(prior, marks, *, adds: bool) -> None:
    """ratchet.seed_ratchet (adds) and update_ratchet (adds none): no mark rises."""
    began = perf_counter_ns()
    before = _highest(prior)
    for entry in marks:
        was = before.get((entry.path, entry.long_name))
        problem = _rise(entry, was, adds)
        if problem:
            _stop(problem, f"{entry.path}\t{entry.long_name} (mark {was!r} -> {entry.crap!r})",
                  MARKS_KEPT)
    _spent("marks", began)


def four_places(mark: float) -> bool:
    """A mark as the file holds it: finite, at four decimals. Not a bound on its
    size: a hand-typed mark below any CRAP (0, -1) is read and written back as it
    stands, and every mark crapkit computes comes from a row check_rows passed."""
    return math.isfinite(mark) and float(f"{mark:.4f}") == mark


def check_dump(entries) -> None:
    """ratchet.dump_ratchet: every mark it writes reads back as the same number."""
    began = perf_counter_ns()
    for e in entries:
        if not four_places(e.crap):
            _stop("a mark must be a finite number held at four decimals",
                  f"{e.path}\t{e.long_name} (mark {e.crap!r})", MARKS_KEPT)
    _spent("dump", began)


# --- the worklist ----------------------------------------------------------------------


def _churn_problem(e, active: bool) -> str | None:
    if (e.commits > 0) != active:
        return "active rows have commits in the window and dormant rows have none"
    if not (0 <= e.authors <= e.commits and 0.0 <= e.weight <= e.commits):
        return "churn must satisfy 0 <= authors <= commits and 0 <= weight <= commits"
    return None


def _whole_weights(churn) -> bool:
    """Every commit counts once: the one-timestamp log's weights are whole."""
    return all(float(c.weight).is_integer() for c in churn.values())


def _heavy(entries, churn) -> str | None:
    """A weight past 0.5 per commit, the newest commit's weight, when the log
    carries more than one timestamp."""
    heavy = next((e for e in entries if e.weight > 0.5 * e.commits), None)
    if heavy is None or _whole_weights(churn):
        return None
    return f"{heavy.path} (commits {heavy.commits}, weight {heavy.weight!r})"


def _entry_at(e) -> str:
    return (f"{e.path}:{e.long_name} (commits {e.commits}, authors {e.authors}, "
            f"weight {e.weight!r}, risk {e.risk!r})")


def _check_churn(active, dormant, churn) -> None:
    for entries, is_active in ((active, True), (dormant, False)):
        for e in entries:
            problem = _churn_problem(e, is_active)
            if problem:
                _stop(problem, _entry_at(e), PRINTED)
    heavy = _heavy(active, churn)
    if heavy:
        _stop("a commit weighs at most 0.5 unless every commit shares one timestamp",
              heavy, PRINTED)


def _check_risk_order(active) -> None:
    for above, below in zip(active, active[1:]):
        if not above.risk >= below.risk:
            _stop("the worklist ranks by risk, highest first",
                  f"{_entry_at(below)} ranks under {_entry_at(above)}", PRINTED)


def check_worklist(active, dormant, over: int, churn) -> None:
    """worklist.build_worklist, before the cap: `over` rows of the run sat over
    their ceiling, and a row over its ceiling is admitted whatever its ccn."""
    began = perf_counter_ns()
    admitted = sum(e.remedy not in (None, "ok") for e in chain(active, dormant))
    if admitted != over:
        _stop("every row over its ceiling is admitted to the worklist",
              f"{over} row(s) over their ceiling, {admitted} admitted", PRINTED)
    _check_churn(active, dormant, churn)
    _check_risk_order(active)
    _spent("worklist", began)


# --- totals, grade and the coverage summary --------------------------------------------


def grade_of(over: int, functions: int) -> str:
    """README's band table in integers: `A+` at zero, then the share over."""
    if over == 0:
        return "A+"
    return next((letter for percent, letter in _BANDS if 100 * over < percent * functions), "F")


def check_totals(functions: int, over_target: int, load: float) -> None:
    """digest.totals_from_counts: the three sums every totals reader rounds."""
    began = perf_counter_ns()
    at = f"functions {functions}, over_target {over_target}, crap_load {load!r}"
    if not 0 <= over_target <= functions:
        _stop("0 <= over_target <= functions", at, PRINTED)
    if not (math.isfinite(load) and load >= functions):
        _stop("crap_load is finite and at least one per function, since CRAP >= ccn >= 1",
              at, PRINTED)
    _spent("totals", began)


def check_grade(over: int, functions: int, letter: str, at: str) -> None:
    expected = grade_of(over, functions)
    if letter != expected:
        _stop(f"the grade follows the README band table ({expected})",
              f"{at}: grade {letter} for {over} of {functions} over", PRINTED)


def check_rollup(by_scope: dict) -> None:
    """digest.scope_rollup: each scope's grade against its own counts."""
    began = perf_counter_ns()
    for scope, block in by_scope.items():
        check_grade(block["over_target"], block["functions"], block["grade"], f"scope {scope}")
    _spent("rollup", began)


_SUMMARY_FLAGS = ("measured", "untested", "excluded", "no_lane", "cc_only")


def _bucket_problem(summary: dict) -> str | None:
    counted = sum(summary[flag] for flag in _SUMMARY_FLAGS)
    if counted != summary["functions"]:
        return f"the five flag counts sum to the functions scored ({counted} counted)"
    return None


def check_summary(summary: dict, judged: int) -> None:
    """cli/scoring._coverage_summary: `judged` is how many rows the grade and
    the crap_load are over. A partial run leaves out the scopes no lane
    measured, so its load is bounded by the judged rows, not every function."""
    began = perf_counter_ns()
    at = f"run {summary['run_id']}"
    problem = _bucket_problem(summary)
    if problem:
        _stop(problem, f"{at}, functions {summary['functions']}", PRINTED)
    if not summary["over_target"] <= judged <= summary["functions"]:
        _stop("over_target <= judged rows <= functions", f"{at}, judged {judged}", PRINTED)
    check_totals(judged, summary["over_target"], summary["crap_load"])
    check_grade(summary["over_target"], judged, summary["grade"], at)
    _spent("summary", began)


# --- the packet ------------------------------------------------------------------------


_PACKET = COST.setdefault("packet", [0, 0])


def check_rejudged(row, ceiling: int) -> None:
    """packet.rejudged: the remedy the packet prints against today's ceiling.
    The row's own CRAP and coverage were checked when its run was stored.
    next-item rejudges every admitted row (62,877 on a large repo), so the check
    tests the one table lookup it needs and adds to its tally in place."""
    began = perf_counter_ns()
    if row.remedy not in _expected_remedies(row.ccn, row.crap, ceiling):
        _stop(remedy_problem(row, ceiling), where(row), PRINTED)
    _PACKET[0] += 1
    _PACKET[1] += perf_counter_ns() - began


def _splits_problem(ccn: int, ceiling: int, splits: int) -> str | None:
    if ccn <= ceiling:
        return None if splits == 0 else "est_splits is 0 when ccn <= target"
    if (splits - 1) * ceiling < ccn <= splits * ceiling:
        return None
    return "est_splits is ceil(ccn / target) when ccn > target"


def _paths_problem(ccn: int, cov: float, paths: int) -> str | None:
    if 0 <= paths <= ccn and abs(paths - (1.0 - cov) * ccn) <= 0.5 + _REL:
        return None
    return "est_uncovered_paths is (1 - cov) * ccn rounded, from 0 to ccn"


def check_budget(row, ceiling: int, budget: dict) -> None:
    """packet.budget: est_splits and est_uncovered_paths."""
    began = perf_counter_ns()
    problem = (_splits_problem(row.ccn, ceiling, budget["est_splits"])
               or _paths_problem(row.ccn, row.cov, budget["est_uncovered_paths"]))
    if problem:
        _stop(problem, f"{row.path}:{row.long_name} (ccn {row.ccn}, cov {row.cov!r}, target "
                       f"{ceiling}, budget {budget})", PRINTED)
    _spent("packet", began)


# --- gates -----------------------------------------------------------------------------


def _scopes(in_scope: dict) -> dict:
    return {path: scope for scope, paths in in_scope.items() for path in paths}


def _check_over(violations, ceiling_of_path, site: str) -> None:
    for v in violations:
        ceiling = ceiling_of_path(v.path)
        if not v.ccn > ceiling:
            _stop("a gate violation has ccn over its file's ceiling",
                  f"{v.path}:{v.long_name} (ccn {v.ccn}, ceiling {ceiling}, {site})", PRINTED)


def check_violations(violations, in_scope: dict, ceiling_of) -> None:
    """hook's violation list: `in_scope` maps each scope to its files, and
    `ceiling_of(scope)` is the parsed config's ceiling rule."""
    began = perf_counter_ns()
    scope_of = _scopes(in_scope)
    _check_over(violations, lambda path: ceiling_of(scope_of[path]), "hook-precommit")
    _spent("hook", began)


def check_advisory(breaches, ceiling: int, in_scope: dict, ceiling_of) -> None:
    """claude-hook's advisory: one file, the ceiling its head line prints."""
    began = perf_counter_ns()
    scope = next(scope for scope, paths in in_scope.items() if paths)
    if ceiling != ceiling_of(scope):
        _stop("the advisory names its scope's ceiling", f"scope {scope}, ceiling {ceiling}", PRINTED)
    _check_over(breaches, lambda path: ceiling, "claude-hook")
    _spent("advisory", began)


def check_gate(overlay, breaches, ceiling_of) -> None:
    """rescore --gate: the rescored rows, then the breaches against their scopes' ceilings."""
    began = perf_counter_ns()
    check_rows(overlay, ceiling_of, kept=PRINTED, site="gate")
    scope_of = {row.path: row.scope for row in overlay}
    _check_over(breaches, lambda path: ceiling_of(scope_of[path]), "rescore --gate")
    _spent("gate", began)


# --- the verify verdict ----------------------------------------------------------------


def verdict_exit(verdict) -> int:
    """README's exit table over a verdict's four finding lists."""
    return next((code for code, field in _PRECEDENCE if getattr(verdict, field)), 0)


def check_verdict(verdict, exit_code: int, *, kept: str) -> None:
    """cli/verifying: the exit the verdict reports, and the `ok` the run stores."""
    began = perf_counter_ns()
    expected = verdict_exit(verdict)
    if exit_code != expected or verdict.ok != (expected == 0):
        counts = ", ".join(f"{field} {len(getattr(verdict, field))}" for _, field in _PRECEDENCE)
        _stop(f"verify exits {expected} by the README precedence 6 > 7 > 8 > 9 > 0, "
              "and stores ok only with exit 0",
              f"exit {exit_code}, ok {verdict.ok}, {counts}", kept)
    _spent("verdict", began)
