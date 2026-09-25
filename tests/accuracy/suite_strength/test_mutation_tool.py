"""tools/accuracy/mutation.py: survivor keys, the survivor-set gate, floors and
what the nightly run mutates.

The expected values are worked out here: keys are sha256 over diff lines this
file writes out, the gate is checked against a set model written from the
plan's rule (a new survivor fails, a gone survivor is removed, an equivalent
that matches nothing fails), and the weekly boundary is accuracy.yml's cron
`0 6 * * 6`.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import inspect
import io
import json
import os
import re
from pathlib import Path
import sys
import time

from hypothesis import given, strategies as st
import pytest

from accuracy.kit.settings import pure

REPO = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("accuracy_mutation_tool",
                                                  REPO / "tools" / "accuracy" / "mutation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mutation = _load()

# What `mutmut show` prints for a mutant of score.crap: the original function
# and the mutant, both renamed by mutmut, as a unified diff.
SHOW_3 = """\
--- src/crapkit/score.py
+++ src/crapkit/score.py
@@ -1,2 +1,2 @@
-def x_crap__mutmut_orig(ccn: int, cov: float) -> float:
-    return ccn * ccn * (1.0 - cov) ** 3 + ccn
+def x_crap__mutmut_3(ccn: int, cov: float) -> float:
+    return ccn * ccn * (1.0 - cov) ** 3 - ccn"""
# The same mutant after lines were added above the function and mutmut
# numbered it 7.
SHOW_7 = SHOW_3.replace("@@ -1,2 +1,2 @@", "@@ -24,2 +24,2 @@").replace("mutmut_3", "mutmut_7")
KEPT = ["-def x_crap__mutmut(ccn: int, cov: float) -> float:",
        "-    return ccn * ccn * (1.0 - cov) ** 3 + ccn",
        "+def x_crap__mutmut(ccn: int, cov: float) -> float:",
        "+    return ccn * ccn * (1.0 - cov) ** 3 - ccn"]


# --- keys ---------------------------------------------------------------------------------------

def test_a_key_is_sha256_over_the_changed_lines_without_numbering():
    assert mutation.normalized_diff(SHOW_3) == KEPT
    assert mutation.mutant_key(SHOW_3) == hashlib.sha256("\n".join(KEPT).encode()).hexdigest()


def test_line_renumbering_keeps_survivor_keys():
    assert mutation.mutant_key(SHOW_7) == mutation.mutant_key(SHOW_3)
    assert mutation.mutant_key(SHOW_3.replace("\n", "\r\n")) == mutation.mutant_key(SHOW_3)


def test_another_operator_is_another_key():
    other = SHOW_3.replace("** 3 - ccn", "** 3 * ccn")

    assert mutation.mutant_key(other) != mutation.mutant_key(SHOW_3)


@pytest.mark.parametrize("name, expected", [
    ("crapkit.score.x_crap__mutmut_3", ("crapkit.score", "crap")),
    ("crapkit.store.xǁSnapshotStoreǁwrite_run__mutmut_12",
     ("crapkit.store", "SnapshotStore.write_run")),
    ("crapkit.cli.verifying.x__settle_verify__mutmut_1",
     ("crapkit.cli.verifying", "_settle_verify")),
])
def test_a_mutant_name_splits_into_module_and_function(name, expected):
    assert mutation.split_name(name) == expected


@pytest.mark.parametrize("name", ["crap__mutmut_3", "crapkit.score.crap__mutmut_3",
                                  "crapkit.score.x_crap", "crapkit.store.xǁA__mutmut_1"])
def test_a_name_mutmut_did_not_write_is_refused(name):
    with pytest.raises(mutation.MutationError):
        mutation.split_name(name)


def test_a_module_resolves_under_src_first():
    assert mutation.module_path("crapkit.score") == "src/crapkit/score.py"
    assert mutation.module_path("tools.accuracy.retro") == "tools/accuracy/retro.py"
    with pytest.raises(mutation.MutationError):
        mutation.module_path("crapkit.no_such_module")


@pytest.mark.parametrize("path, qualname, glob", [
    ("src/crapkit/analyze.py", "_record", "crapkit.analyze.x__record__mutmut_*"),
    ("src/crapkit/store.py", "SnapshotStore.write_run",
     "crapkit.store.xǁSnapshotStoreǁwrite_run__mutmut_*"),
    ("tools/accuracy/retro.py", "digest", "tools.accuracy.retro.x_digest__mutmut_*"),
])
def test_a_function_s_glob_is_the_name_mutmut_gives_its_mutants(path, qualname, glob):
    assert mutation.mutmut_glob(path, qualname) == glob


# --- the gate -------------------------------------------------------------------------------------

MODULES = ("src/crapkit/score.py", "src/crapkit/digest.py")
FUNCTIONS = ("crap", "totals")
KEYS = tuple(hashlib.sha256(bytes([n])).hexdigest() for n in range(4))
STATUSES = ("killed", "survived", "timeout", "no tests")


def _row(module, function, key):
    return {"module": module, "function": function, "diff_sha256": key}


def _result(module, function, key, status):
    return mutation.Result(f"{module}:{function}:{key[:6]}", module, function, status, key)


results = st.lists(st.tuples(st.sampled_from(MODULES), st.sampled_from(FUNCTIONS),
                             st.sampled_from(KEYS), st.sampled_from(STATUSES)),
                   unique_by=lambda t: t[:3]).map(lambda rows: [_result(*row) for row in rows])
listed = st.lists(st.tuples(st.sampled_from(MODULES), st.sampled_from(FUNCTIONS),
                            st.sampled_from(KEYS)), unique=True)


def _alive(results):
    """A mutant no test reaches (mutmut: `no tests`) lives as surely as one that survives."""
    return {(r.module, r.function, r.key) for r in results
            if r.status in ("survived", "no tests")}


def _in_run(rows, mutated):
    return {row for row in rows if row[0] in mutated}


def _model(results, survivors, equivalents):
    """The rule, from the plan: a survivor on neither list is new and fails; a
    listed survivor of a mutated module that did not survive is gone; an
    equivalent of a mutated module that no mutant matches is an orphan."""
    alive, every = _alive(results), {(r.module, r.function, r.key) for r in results}
    mutated = {r.module for r in results}
    mine = _in_run(equivalents, mutated)
    return (sorted(alive - set(survivors) - set(equivalents)),
            sorted(_in_run(survivors, mutated) - alive),
            sorted((mine & every) - alive), sorted(mine - every))


@given(results, listed, listed)
@pure
def test_the_gate_matches_the_survivor_set_model(results, survivors, equivalents):
    verdict = mutation.gate(results, [_row(*s) for s in survivors],
                            [_row(*e) for e in equivalents], canary=False)

    got = (list(verdict.new), list(verdict.gone), list(verdict.killed_equivalents),
           list(verdict.orphan_equivalents))
    assert got == _model(results, survivors, equivalents)
    assert verdict.passed == (not got[0] and not got[3])


def _crap(key, status):
    return _result("src/crapkit/score.py", "crap", key, status)


def test_a_new_survivor_fails_and_a_listed_one_passes():
    run = [_crap(KEYS[0], "survived"), _crap(KEYS[1], "killed")]

    assert not mutation.gate(run, [], []).passed
    assert mutation.gate(run, [_row("src/crapkit/score.py", "crap", KEYS[0])], [],
                         canary=False).passed


def test_the_canary_voids_a_run_where_a_score_crap_mutant_survives():
    assert mutation.gate([_crap(KEYS[0], "killed")], [], []).void == ""
    assert "survived" in mutation.gate([_crap(KEYS[0], "timeout")], [], []).void
    assert "not mutated" in mutation.gate([], [], []).void


@pytest.mark.parametrize("status", ["not checked", "suspicious"])
def test_a_mutant_mutmut_never_judged_voids_the_run(status):
    """mutmut leaves every mutant `not checked` when its stats run fails: no test
    ran, so the run is no evidence, never a pass with nothing new."""
    verdict = mutation.gate([_crap(KEYS[0], "killed"), _crap(KEYS[1], status)], [], [],
                            canary=False)

    assert not verdict.passed
    assert verdict.void == f"1 mutant was never judged (mutmut says {status}): " + (
        f"src/crapkit/score.py:crap:{KEYS[1][:6]}")


def test_a_mutant_no_test_reaches_is_a_survivor():
    verdict = mutation.gate([_crap(KEYS[0], "no tests")], [], [], canary=False)

    assert verdict.new == (("src/crapkit/score.py", "crap", KEYS[0]),)


def test_update_removes_gone_survivors_and_killed_equivalents():
    survivors = [_row("src/crapkit/score.py", "crap", KEYS[0]),
                 _row("src/crapkit/other.py", "f", KEYS[1])]
    equivalents = [{**_row("src/crapkit/score.py", "crap", KEYS[2]), "evidence": "e",
                    "strategy": "s", "checked": "d"}]
    verdict = mutation.gate([_crap(KEYS[0], "killed"), _crap(KEYS[2], "killed")], survivors,
                            equivalents, canary=False)

    assert mutation.updated_survivors(survivors, verdict) == survivors[1:]
    assert mutation.updated_equivalents(equivalents, verdict) == []


def test_every_verdict_line_names_the_key_and_what_to_do():
    verdict = mutation.Verdict(new=(("m.py", "f", "k1"),), gone=(("m.py", "g", "k2"),),
                               orphan_equivalents=(("m.py", "h", "k3"),), void="why")

    lines = mutation.verdict_lines(verdict)

    assert lines[0] == "void: why"
    assert "new survivor m.py f k1" in lines[1] and "survivors.tsv" in lines[1]
    assert "m.py h k3 matches no mutant" in lines[2]
    assert "m.py g k2 now dies" in lines[3]


# --- floors ----------------------------------------------------------------------------------------

GROUPS = [{"group": "core", "paths": "src/crapkit/score.py, src/crapkit/digest.py", "floor": "95",
           "source": "plan"},
          {"group": "readers", "paths": "src/crapkit/lizard*.py", "floor": "85", "source": "plan"}]


def test_a_floor_counts_kills_over_mutants_after_equivalents():
    run = [_crap(KEYS[n], status) for n, status in enumerate(("killed",) * 3 + ("survived",))]
    equivalent = [_row("src/crapkit/score.py", "crap", KEYS[3])]

    core, readers = mutation.floors(run, equivalent, GROUPS)

    assert (core.killed, core.counted, core.rate, core.ok) == (3, 3, 100.0, True)
    assert (readers.rate, readers.ok) == (None, True)


def test_a_timeout_is_not_a_kill():
    run = [_crap(KEYS[0], "killed"), _crap(KEYS[1], "timeout")]

    (core, _) = mutation.floors(run, [], GROUPS)

    assert (core.rate, core.ok) == (50.0, False)


# --- what the nightly run mutates ------------------------------------------------------------------

def _utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


@pytest.mark.parametrize("now, expected", [
    (_utc(2026, 9, 26, 6, 0), _utc(2026, 9, 26, 6, 0)),     # Saturday at the cron minute
    (_utc(2026, 9, 26, 5, 59), _utc(2026, 9, 19, 6, 0)),    # a minute before it
    (_utc(2026, 9, 24, 12, 0), _utc(2026, 9, 19, 6, 0)),    # a Thursday
    (_utc(2026, 9, 27, 0, 0), _utc(2026, 9, 26, 6, 0)),     # Sunday
])
def test_the_last_weekly_run_is_the_latest_saturday_six_utc(now, expected):
    assert mutation.last_weekly(now) == expected


HUNKS = """\
diff --git a/m.py b/m.py
@@ -3 +3 @@ def a():
-    return 1
+    return 2
@@ -10,0 +11,2 @@ def b():
+    x = 1
+    y = 2
@@ -20,2 +21,0 @@ def c():
-    gone = 1
-    also = 2
"""


def test_changed_lines_read_every_hunk_s_new_side():
    assert mutation.changed_lines(HUNKS) == {3, 11, 12, 21}


SOURCE = '''\
def a():
    return 2


class Store:
    def write(self):
        return 1

    def read(self):
        return 0


def c():
    pass
'''


def test_a_changed_line_names_its_function_or_method():
    assert mutation.touched_functions(SOURCE, {2}) == ["a"]
    assert mutation.touched_functions(SOURCE, {7, 13}) == ["Store.write", "c"]
    assert mutation.touched_functions(SOURCE, {4}) == []


MODULE_NAMES = [f"src/m{n}.py" for n in range(30)]


@given(st.lists(st.sampled_from(MODULE_NAMES), unique=True, max_size=20), st.integers(1, 8))
@pure
def test_shards_partition_the_modules(modules, of):
    parts = [mutation.shard(modules, number, of) for number in range(1, of + 1)]

    assert sorted(item for part in parts for item in part) == sorted(modules)


def test_a_shard_outside_the_range_is_refused():
    with pytest.raises(mutation.MutationError):
        mutation.shard(["a"], 0, 8)


def test_release_coverage_names_changed_functions_no_complete_diff_mutated():
    diffs = [{"complete": True, "functions": [["src/crapkit/score.py", "crap"]]},
             {"complete": False, "functions": [["src/crapkit/digest.py", "totals"]]}]
    changed = [("src/crapkit/score.py", "crap"), ("src/crapkit/digest.py", "totals")]

    assert mutation.uncovered(changed, diffs) == ["src/crapkit/digest.py:totals"]


def _receipts(directory: Path, *receipts: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for index, receipt in enumerate(receipts):
        (directory / f"r{index}.json").write_text(json.dumps(receipt), encoding="utf-8")
    return directory


HEAD_A, HEAD_B = "a" * 40, "b" * 40


def test_the_release_row_reads_every_weekly_shard_and_diff_receipt_in_one_directory(tmp_path):
    weekly = [{"kind": "weekly", "head": HEAD_A, "shard": shard, "of": 2} for shard in (1, 2)]
    diff = {"kind": "diff", "complete": True, "functions": []}
    weeklies, diffs = mutation.receipts_in(_receipts(tmp_path / "r", *weekly, diff))

    assert (mutation.weekly_head(weeklies), diffs) == (HEAD_A, [diff])


@pytest.mark.parametrize("weekly, says", [
    ([{"kind": "weekly", "head": HEAD_A, "shard": 1, "of": 3}], "weekly shards [2, 3] have no"),
    ([{"kind": "weekly", "head": HEAD_A, "shard": 1, "of": 2},
      {"kind": "weekly", "head": HEAD_B, "shard": 2, "of": 2}], "measured 2 heads"),
])
def test_a_weekly_run_the_receipts_do_not_hold_whole_is_refused(weekly, says):
    with pytest.raises(mutation.MutationError, match=re.escape(says)):
        mutation.weekly_head(weekly)


def test_no_weekly_receipt_is_an_infra_miss_that_names_the_download(tmp_path, capsys):
    code = mutation.main(["covered", "--receipts", str(_receipts(tmp_path / "empty"))])

    assert code == 3
    assert "gh run download" in capsys.readouterr().err


# --- equivalence evidence --------------------------------------------------------------------------

def _max(a, b):
    return a if a >= b else b


def _max_mutant(a, b):
    return a if a > b else b     # `>=` to `>`: equal inputs return the same value


def _min(a, b):
    return a if a < b else b


def test_an_equivalent_mutant_gets_its_evidence_line():
    pairs = st.tuples(st.integers(-1000, 1000), st.integers(-1000, 1000))

    assert mutation.equivalence_evidence(_max, _max_mutant, pairs, examples=300) == (
        "300 examples, derandomized, no difference")


def test_a_small_space_is_searched_whole_and_says_how_many_inputs_it_held():
    """Eleven values squared is 121 inputs: Hypothesis stops when it has tried them all."""
    pairs = st.tuples(st.integers(-5, 5), st.integers(-5, 5))

    assert mutation.equivalence_evidence(_max, _max_mutant, pairs, examples=300) == (
        "121 examples, derandomized, no difference")


def test_a_mutant_that_differs_is_refused_with_the_input():
    pairs = st.tuples(st.integers(-5, 5), st.integers(-5, 5))

    with pytest.raises(mutation.MutationError, match="differs on"):
        mutation.equivalence_evidence(_max, _min, pairs, examples=300)


# --- the killer suite and the tables ------------------------------------------------------------------

def test_the_killer_suite_imports_this_tree_s_code_first(tmp_path):
    env = mutation.killer_env(tmp_path, {"PYTHONPATH": "elsewhere"})

    assert env["PYTHONPATH"].split(mutation.os.pathsep) == [str(tmp_path / "src"),
                                                            str(tmp_path / "tests"), "elsewhere"]
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_the_killer_suite_runs_the_push_tier_whatever_tier_started_it(tmp_path):
    """A nightly run.py that starts the killer must not hand it 20,000-example
    Hypothesis settings per mutant: the killer's cost is the push tier's."""
    env = mutation.killer_env(tmp_path, {"CRAPKIT_ACCURACY_TIER": "nightly"})

    assert env["CRAPKIT_ACCURACY_TIER"] == "push"


def test_the_killer_suite_deselects_every_dependent_method():
    argv = mutation.killer_argv([])
    marks = argv[argv.index("-m", 3) + 1]

    for marker in ("process", "golden", "change_control", "cross_surface"):
        assert f"not {marker}" in marks
    assert argv[3:5] == ["tests/unit", "tests/accuracy"] and "-x" in argv


def test_the_killer_hands_pytest_every_argument_after_its_name(monkeypatch):
    """`mutation.py killer [PYTEST ARGS...]`: an argument that starts with a dash
    is pytest's, not the tool's."""
    handed = []
    monkeypatch.setitem(mutation.COMMANDS, "killer", lambda args: handed.append(args.pytest) or 0)

    assert mutation.main(["killer", "--deselect", "tests/unit/a.py::t", "-k", "x"]) == 0
    assert mutation.main(["killer"]) == 0
    assert handed == [["--deselect", "tests/unit/a.py::t", "-k", "x"], []]
    with pytest.raises(SystemExit):
        mutation.main(["key", "--deselect", "x"])


def test_a_table_round_trips_and_a_wrong_header_is_refused(tmp_path):
    path = tmp_path / "survivors.tsv"
    rows = [{"module": "m.py", "function": "f", "diff_sha256": KEYS[0], "reason": "why",
             "added": "2026-09-24"}]

    mutation.write_table(path, mutation.SURVIVOR_COLUMNS, rows)

    assert mutation.read_table(path, mutation.SURVIVOR_COLUMNS) == rows
    with pytest.raises(mutation.MutationError, match="header"):
        mutation.read_table(path, mutation.EQUIVALENT_COLUMNS)


def test_gate_command_exits_one_on_a_new_survivor(tmp_path, monkeypatch, capsys):
    for name, columns in (("survivors.tsv", mutation.SURVIVOR_COLUMNS),
                          ("equivalent.tsv", mutation.EQUIVALENT_COLUMNS),
                          ("floors.tsv", mutation.FLOOR_COLUMNS)):
        mutation.write_table(tmp_path / name, columns, [])
    monkeypatch.setattr(mutation, "TABLES", tmp_path)
    receipt = tmp_path / "weekly.json"
    row = _crap(KEYS[0], "survived")
    receipt.write_text(json.dumps({"results": [row.__dict__]}), encoding="utf-8")

    assert mutation.main(["gate", str(receipt), "--no-canary"]) == 1
    assert f"new survivor src/crapkit/score.py crap {KEYS[0]}" in capsys.readouterr().out


def _tables(directory: Path, survivors=(), floors=()) -> None:
    for name, columns, rows in (("survivors.tsv", mutation.SURVIVOR_COLUMNS, list(survivors)),
                                ("equivalent.tsv", mutation.EQUIVALENT_COLUMNS, []),
                                ("floors.tsv", mutation.FLOOR_COLUMNS, list(floors))):
        mutation.write_table(directory / name, columns, rows)


def test_a_run_below_a_floor_fails_though_every_survivor_is_listed(tmp_path, monkeypatch, capsys):
    """One kill in two mutants is 50 percent against score.py's 95 percent floor."""
    listed = {**_row("src/crapkit/score.py", "crap", KEYS[1]), "reason": "why",
              "added": "2026-09-25"}
    _tables(tmp_path, [listed], [{**GROUPS[0], "paths": "src/crapkit/score.py"}])
    monkeypatch.setattr(mutation, "TABLES", tmp_path)
    receipt = tmp_path / "weekly.json"
    rows = [_crap(KEYS[0], "killed").__dict__, _crap(KEYS[1], "survived").__dict__]
    receipt.write_text(json.dumps({"results": rows}), encoding="utf-8")

    assert mutation.main(["gate", str(receipt), "--no-canary"]) == 1
    assert "floor core: 50.0% (1/2), floor 95.0% BELOW" in capsys.readouterr().out


# --- the second config: the accuracy tools and kit.exact ---------------------------------------------

PYPROJECT = """\
[project]
name = "crapkit"

[tool.mutmut]
paths_to_mutate = ["src/crapkit/score.py"]
also_copy = ["tests/"]

[tool.coverage.run]
patch = ["subprocess"]
"""


def test_the_stage_config_replaces_only_the_mutmut_table():
    targets = {"tools/accuracy/retro.py": ("tests/accuracy/suite_strength/test_retro_tool.py",)}
    text = mutation.stage_config(PYPROJECT, targets, ["README.md", "docs", "tests", "tools"])
    parsed = mutation.tomllib.loads(text)

    assert parsed["project"] == {"name": "crapkit"}
    assert parsed["tool"]["coverage"] == {"run": {"patch": ["subprocess"]}}
    assert parsed["tool"]["mutmut"]["source_paths"] == ["tools/accuracy/retro.py"]
    assert parsed["tool"]["mutmut"]["pytest_add_cli_args_test_selection"] == [
        "tests/accuracy/suite_strength/test_retro_tool.py"]
    assert parsed["tool"]["mutmut"]["pytest_add_cli_args"][-2:] == ["-m", mutation.FLOOR_SUITE]
    assert parsed["tool"]["mutmut"]["also_copy"] == ["README.md", "docs", "tests", "tools"]


def test_the_launcher_s_diffs_read_as_a_map_and_mutmut_s_chatter_is_left_out():
    printed = ('     also copying tests\n'
               f'{json.dumps(["crapkit.score.x_crap__mutmut_3", SHOW_3])}\n'
               '["crapkit.score.x_crap__mutmut_4", ""]\n')

    assert mutation.parse_diffs(printed) == {"crapkit.score.x_crap__mutmut_3": SHOW_3,
                                             "crapkit.score.x_crap__mutmut_4": ""}


def test_survivors_unreached_mutants_and_timeouts_carry_a_key_and_kills_do_not():
    statuses = {"a": "killed", "b": "survived", "c": "no tests", "d": "timeout",
                "e": "caught by type check", "f": "skipped"}

    assert mutation.keyed_names(statuses) == ["b", "c", "d"]


@pytest.mark.parametrize("module", ["tools/accuracy/mutation.py", "tools/accuracy/retro.py",
                                    "tools/accuracy/run.py", "tests/accuracy/kit/exact.py"])
def test_no_mutated_function_has_a_name_mutmut_misreads(module):
    """mutmut names a mutant x_<function>__mutmut_<n> and reads the function back
    up to the first `__mutmut`, so a function called _mutmut had no diff for any
    of its mutants and stopped the tools run. Under mutmut the file read here is
    mutmut's copy, whose generated x_..._mutmut_<n> functions are left out."""
    written = _written_functions(REPO / module)

    assert [name for name in written if "__mutmut" in f"x_{name}"] == []
    assert "_run_mutmut" in written or module != "tools/accuracy/mutation.py"


MUTMUT_MADE = re.compile(r"__mutmut_(?:orig|\d+)$")
DEFS = (mutation.ast.FunctionDef, mutation.ast.AsyncFunctionDef)


def _written_functions(path: Path) -> list[str]:
    """Every function a module defines, less the ones mutmut generated in its copy."""
    tree = mutation.ast.parse(path.read_text(encoding="utf-8"))
    names = [node.name for node in mutation.ast.walk(tree) if isinstance(node, DEFS)]
    return [name for name in names if not MUTMUT_MADE.search(name)]


def test_a_weekly_shard_runs_the_independent_suite_on_its_modules_and_the_canary():
    """The floors are computed on tests/unit and the accuracy tests, golden,
    change_control and cross_surface ones left out (the plan's mutation section).
    The repo's own [tool.mutmut] names no test selection and no marker, so mutmut
    would run all of tests/, e2e and goldens included."""
    targets = mutation.calc_targets(["src/crapkit/digest.py"])
    parsed = mutation.tomllib.loads(mutation.stage_config(PYPROJECT, targets, ["src", "tests"]))

    assert parsed["tool"]["mutmut"]["source_paths"] == ["src/crapkit/digest.py",
                                                         "src/crapkit/score.py"]
    assert parsed["tool"]["mutmut"]["pytest_add_cli_args_test_selection"] == [
        "tests/accuracy", "tests/unit"]
    assert parsed["tool"]["mutmut"]["pytest_add_cli_args"][-2:] == ["-m", mutation.FLOOR_SUITE]


def test_the_weekly_suite_runs_the_push_tier_on_this_platform_only():
    """Every tier's tests would bring in the ones marked for another platform,
    which fail mutmut's stats run and leave every mutant unjudged."""
    env = mutation.calc_env({"CRAPKIT_ACCURACY_TIER": "nightly", "CRAPKIT_ACCURACY_COLLECT_ALL": "1"})

    assert env["CRAPKIT_ACCURACY_TIER"] == "push"
    assert "CRAPKIT_ACCURACY_COLLECT_ALL" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


@pytest.mark.process
def test_the_stage_copies_every_top_level_entry_its_tests_may_read(tmp_path):
    """A tools test that reads README.md failed mutmut's stats run in a stage that
    copied only tests/ and tools/, and every mutant stayed `not checked`."""
    for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@t"]):
        mutation._git(tmp_path, *args)
    for name in ("README.md", "docs/a.md", "tools/x.py", ".gitignore"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    mutation._git(tmp_path, "add", "-A")
    mutation._git(tmp_path, "commit", "-qm", "c")
    (tmp_path / "mutants").mkdir()

    assert mutation.stage_copies(tmp_path) == [".gitignore", "README.md", "docs", "tools"]


def test_the_floor_suite_leaves_out_only_the_dependent_methods():
    assert mutation.FLOOR_SUITE == "not golden and not change_control and not cross_surface"


@pytest.mark.parametrize("dotted, path", [
    ("accuracy.kit.exact", "tests/accuracy/kit/exact.py"),
    ("tools.accuracy.retro", "tools/accuracy/retro.py"),
])
def test_a_stage_module_name_resolves_to_its_repo_path(dotted, path):
    assert mutation.module_path(dotted) == path


def test_only_the_targets_whose_source_is_here_are_staged(tmp_path):
    (tmp_path / "tools" / "accuracy").mkdir(parents=True)
    (tmp_path / "tools" / "accuracy" / "retro.py").write_text("", encoding="utf-8")
    targets = {"tools/accuracy/retro.py": ("t1",), "tools/accuracy/gone.py": ("t2",)}

    assert mutation.present(targets, tmp_path) == {"tools/accuracy/retro.py": ("t1",)}


def test_the_launcher_names_a_module_the_way_its_tests_import_it(tmp_path):
    """mutmut strips `src.` from a path's dotted name; the launcher also strips
    `tests.`, and a mutated file a test loads by path takes that same name."""
    namespace: dict = {}
    source = mutation.LAUNCHER.replace("from mutmut.__main__ import cli\ncli()\n", "")
    exec(compile(source.split("# --- mutmut ---")[0], "launcher", "exec"), namespace)

    assert namespace["canonical"]("tests/accuracy/kit/exact.py") == "accuracy.kit.exact"
    assert namespace["canonical"]("tools/accuracy/run.py") == "tools.accuracy.run"


# --- reading mutmut's results, through a launcher that stands in for mutmut -------------------------
#
# collect() reads mutmut's .meta files and asks the launcher for the diffs of the
# mutants that live; _rerun_timeouts() asks it to run the timeouts again, one at
# a time. FAKE_LAUNCHER plays mutmut: `diffs` answers each name with a diff of
# its own, `run` rewrites the meta file as if the reruns were killed, and every
# call is logged, so a test sees how often mutmut would have started.

FAKE_LAUNCHER = '''\
import json, os, pathlib, sys
here = pathlib.Path.cwd()
with open(here / "calls.log", "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:2] == ["diffs"]:
    for name in sys.stdin.read().split():
        if "silent" not in name:
            print(json.dumps([name, "-    return a\\n+    return " + name]))
elif sys.argv[1:2] == ["run"]:
    with open(here / "env.log", "a", encoding="utf-8") as log:
        log.write(os.environ.get("MUTATION_PROBE", "-") + "\\n")
    meta = here / "mutants" / "src" / "crapkit" / "score.py.meta"
    codes = json.loads(meta.read_text(encoding="utf-8"))
    for name in sys.argv[2:]:
        if name in codes["exit_code_by_key"]:
            codes["exit_code_by_key"][name] = 1
    meta.write_text(json.dumps(codes), encoding="utf-8")
'''
META = {"crapkit.score.x_crap__mutmut_1": 1, "crapkit.score.x_crap__mutmut_2": 0,
        "crapkit.score.x_crap__mutmut_3": 33, "crapkit.score.x_crap__mutmut_4": 36,
        "crapkit.score.x_crap__mutmut_5": None}
DIGEST_META = {"crapkit.digest.x_totals__mutmut_1": 0}


def _mutmut_tree(tmp_path: Path, meta: dict = META) -> Path:
    for module in ("score", "digest"):
        (tmp_path / "src" / "crapkit").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "crapkit" / f"{module}.py").write_text("x = 1\n", encoding="utf-8")
    for module, codes in (("score", meta), ("digest", DIGEST_META)):
        path = tmp_path / "mutants" / "src" / "crapkit" / f"{module}.py.meta"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"exit_code_by_key": codes}), encoding="utf-8")
    (tmp_path / "fake_launch.py").write_text(FAKE_LAUNCHER, encoding="utf-8")
    return tmp_path


def _calls(tree: Path) -> list[list[str]]:
    log = tree / "calls.log"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if (
        log.is_file()) else []


def _key_of(name: str) -> str:
    return mutation.mutant_key(f"-    return a\n+    return {name}")


def test_collect_reads_the_meta_files_and_keys_every_live_mutant_in_one_call(tmp_path):
    tree = _mutmut_tree(tmp_path)

    rows = mutation.collect(tree, ["crapkit.score.*"], ("fake_launch.py",))

    got = {row.name.rsplit("_", 1)[1]: (row.module, row.function, row.status, row.key)
           for row in rows}
    assert got == {
        "1": ("src/crapkit/score.py", "crap", "killed", ""),
        "2": ("src/crapkit/score.py", "crap", "survived", _key_of("crapkit.score.x_crap__mutmut_2")),
        "3": ("src/crapkit/score.py", "crap", "no tests", _key_of("crapkit.score.x_crap__mutmut_3")),
        "4": ("src/crapkit/score.py", "crap", "timeout", _key_of("crapkit.score.x_crap__mutmut_4")),
        "5": ("src/crapkit/score.py", "crap", "not checked", ""),
    }
    assert _calls(tree) == [["diffs"]]


def test_collect_with_no_glob_reads_every_module(tmp_path):
    rows = mutation.collect(_mutmut_tree(tmp_path), None, ("fake_launch.py",))

    assert sorted({row.module for row in rows}) == ["src/crapkit/digest.py", "src/crapkit/score.py"]


def test_a_live_mutant_the_launcher_gives_no_diff_stops_the_run(tmp_path):
    tree = _mutmut_tree(tmp_path, {"crapkit.score.x_crap__mutmut_silent": 0})

    with pytest.raises(mutation.MutationError, match="no diff for 1 mutant"):
        mutation.collect(tree, ["crapkit.score.*"], ("fake_launch.py",))


def test_a_run_with_only_kills_asks_for_no_diff(tmp_path):
    tree = _mutmut_tree(tmp_path, {"crapkit.score.x_crap__mutmut_1": 1})

    rows = mutation.collect(tree, ["crapkit.score.*"], ("fake_launch.py",))

    assert [row.status for row in rows] == ["killed"] and _calls(tree) == []


@pytest.mark.nightly
def test_a_timeout_gets_one_serial_rerun(tmp_path):
    tree = _mutmut_tree(tmp_path)
    rows = mutation.collect(tree, ["crapkit.score.*"], ("fake_launch.py",))

    again = mutation._rerun_timeouts(tree, rows, ("fake_launch.py",), dict(mutation.os.environ))

    assert ["run", "--max-children", "1", "crapkit.score.x_crap__mutmut_4"] in _calls(tree)
    assert {row.name: row.status for row in again}["crapkit.score.x_crap__mutmut_4"] == "killed"
    assert len(again) == len(rows)


def test_a_run_without_a_timeout_reruns_nothing(tmp_path):
    tree = _mutmut_tree(tmp_path, {"crapkit.score.x_crap__mutmut_1": 1})
    rows = mutation.collect(tree, ["crapkit.score.*"], ("fake_launch.py",))

    assert mutation._rerun_timeouts(tree, rows, ("fake_launch.py",)) == rows
    assert _calls(tree) == []


@pytest.mark.nightly
def test_mutmut_s_exit_code_comes_back_and_a_budget_that_runs_out_reads_minus_one(tmp_path):
    (tmp_path / "exits.py").write_text("import sys, time\ntime.sleep(float(sys.argv[1]))\n"
                                       "sys.exit(7)\n", encoding="utf-8")

    assert mutation._run_mutmut(tmp_path, ["0"], None, ("exits.py",)) == 7
    assert mutation._run_mutmut(tmp_path, ["5"], 0.5, ("exits.py",)) == -1


# --- the commands that read receipts --------------------------------------------------------------

def test_the_floors_command_fails_a_group_below_its_floor(tmp_path, monkeypatch, capsys):
    _tables(tmp_path, floors=[{**GROUPS[0], "paths": "src/crapkit/score.py"}])
    monkeypatch.setattr(mutation, "TABLES", tmp_path)
    receipt = tmp_path / "r.json"
    rows = [_crap(KEYS[0], "killed").__dict__, _crap(KEYS[1], "timeout").__dict__]
    receipt.write_text(json.dumps({"results": rows}), encoding="utf-8")
    passing = tmp_path / "p.json"
    passing.write_text(json.dumps({"results": rows[:1]}), encoding="utf-8")

    assert mutation.main(["floors", str(receipt)]) == 1
    assert "floor core: 50.0% (1/2), floor 95.0% BELOW" in capsys.readouterr().out
    assert mutation.main(["floors", str(passing)]) == 0
    assert "floor core: 100.0% (1/1), floor 95.0% ok" in capsys.readouterr().out


def test_the_covered_command_names_each_changed_function_no_diff_run_mutated(
        tmp_path, monkeypatch, capsys):
    weekly = {"kind": "weekly", "head": HEAD_A, "shard": 1, "of": 1}
    diff = {"kind": "diff", "complete": True, "functions": [["src/crapkit/score.py", "crap"]]}
    receipts = _receipts(tmp_path / "r", weekly, diff)
    seen = []
    monkeypatch.setattr(mutation, "calc_modules", lambda: ["src/crapkit/score.py"])
    monkeypatch.setattr(mutation, "changed_functions", lambda repo, base, modules: seen.append(
        (repo, base, modules)) or [("src/crapkit/score.py", "crap"), ("src/crapkit/score.py", "grade")])

    assert mutation.main(["covered", "--receipts", str(receipts)]) == 1
    assert seen == [(mutation.REPO, HEAD_A, ["src/crapkit/score.py"])]
    assert capsys.readouterr().out == (
        f"mutation: src/crapkit/score.py:grade changed since the weekly run at {HEAD_A[:12]} "
        "and no complete diff run mutated it\n")


def test_the_covered_command_passes_when_every_change_was_mutated(tmp_path, monkeypatch):
    weekly = {"kind": "weekly", "head": HEAD_A, "shard": 1, "of": 1}
    monkeypatch.setattr(mutation, "calc_modules", lambda: [])
    monkeypatch.setattr(mutation, "changed_functions", lambda repo, base, modules: [])

    assert mutation.main(["covered", "--receipts", str(_receipts(tmp_path / "r", weekly))]) == 0


# --- what changed since the weekly run, on a dated repo ------------------------------------------

def _dated_repo(tmp_path: Path, commits: list[tuple[str, str]]) -> Path:
    """One commit per (ISO date, text of m.py)."""
    for args in (["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@t"],
                 ["config", "commit.gpgsign", "false"]):
        mutation._git(tmp_path, *args)
    for date, text in commits:
        (tmp_path / "m.py").write_text(text, encoding="utf-8")
        mutation._git(tmp_path, "add", "m.py")
        env = {**mutation.os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        done = mutation.subprocess.run(["git", "commit", "-qm", date], cwd=tmp_path, env=env,
                                       capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return tmp_path


@pytest.mark.process
def test_the_weekly_base_is_the_newest_commit_before_saturday_six_utc(tmp_path):
    repo = _dated_repo(tmp_path, [("2026-09-18T12:00:00Z", "a = 1\n"),
                                  ("2026-09-20T12:00:00Z", "a = 2\n")])
    friday = mutation._git(repo, "rev-list", "-1", "HEAD~1").strip()

    assert mutation.weekly_base(repo, _utc(2026, 9, 24, 12, 0)) == friday
    with pytest.raises(mutation.MutationError, match="no commit before the weekly run"):
        mutation.weekly_base(repo, _utc(2026, 9, 17, 12, 0))


@pytest.mark.process
def test_changed_functions_name_what_a_diff_from_the_base_touches(tmp_path):
    before = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    repo = _dated_repo(tmp_path, [("2026-09-18T12:00:00Z", before),
                                  ("2026-09-20T12:00:00Z", before.replace("return 2", "return 3"))])
    base = mutation._git(repo, "rev-list", "-1", "HEAD~1").strip()

    assert mutation.changed_functions(repo, base, ["m.py", "gone.py"]) == [("m.py", "b")]


# --- what the tools mutation run left alive in the calc functions ------------------------------------
#
# Each test below pins a value the survivor set of the tools run showed no test
# looked at: an exact message, a rounding, a boundary, a default.

def test_a_key_ignores_trailing_blanks_and_crlf_but_not_leading_ones():
    crlf = SHOW_3.replace("\n", "\r\n")
    indented = SHOW_3.replace("+    return", "+     return")

    assert mutation.normalized_diff(crlf) == KEPT
    assert mutation.normalized_diff(SHOW_3.replace("- ccn", "- ccn   ")) == KEPT
    assert mutation.mutant_key(indented) != mutation.mutant_key(SHOW_3)


def test_an_outcome_is_the_value_or_the_exception_s_type():
    assert mutation._outcome(max, (1, 2)) == ("value", 2)
    assert mutation._outcome(int, ("x",)) == ("raises", "ValueError")


def test_raising_another_exception_type_is_a_difference():
    def parse(text):
        return int(text)

    def parse_mutant(text):
        return {}[text]

    with pytest.raises(mutation.MutationError, match="differs on"):
        mutation.equivalence_evidence(parse, parse_mutant, st.tuples(st.just("x")), examples=5)


def test_equivalence_evidence_takes_ten_thousand_examples_unless_told():
    signature = inspect.signature(mutation.equivalence_evidence)

    assert signature.parameters["examples"].default == mutation.EXAMPLES == 10_000


def test_a_floor_rate_is_rounded_to_two_places():
    run = [_crap(KEYS[0], "killed"), _crap(KEYS[1], "survived"), _crap(KEYS[2], "survived")]

    (core, _) = mutation.floors(run, [], GROUPS)

    assert (core.rate, core.killed, core.counted) == (33.33, 1, 3)


def test_a_missing_first_shard_is_named():
    weekly = [{"kind": "weekly", "head": HEAD_A, "shard": shard, "of": 3} for shard in (2, 3)]

    with pytest.raises(mutation.MutationError, match=r"weekly shards \[1\] have no receipt"):
        mutation.weekly_head(weekly)


def test_two_heads_are_both_named():
    weekly = [{"kind": "weekly", "head": head, "shard": shard, "of": 2}
              for shard, head in ((1, HEAD_A), (2, HEAD_B))]

    with pytest.raises(mutation.MutationError) as refused:
        mutation.weekly_head(weekly)

    assert str(refused.value) == f"the weekly receipts measured 2 heads: {HEAD_A}, {HEAD_B}"


@pytest.mark.parametrize("call, says", [
    (lambda: mutation.split_name("crap__mutmut_3"), "'crap__mutmut_3' is not a mutmut mutant name"),
    (lambda: mutation.split_name("a.b__mutmut_"), "'a.b__mutmut_' is not a mutmut mutant name"),
    (lambda: mutation.split_name("a.x_f__mutmut_x__mutmut_3"),
     "'a.x_f__mutmut_x__mutmut_3' is not a mutmut mutant name"),
    (lambda: mutation.split_name("crapkit.store.xǁA__mutmut_1"),
     "'crapkit.store.xǁA__mutmut_1' names no function mutmut mangles"),
    (lambda: mutation.module_path("crapkit.nope", REPO),
     f"no file for module crapkit.nope under {REPO}"),
    (lambda: mutation.shard(["a.py"], 3, 2), "shard 3 of 2 does not exist"),
])
def test_each_refusal_says_what_it_refused(call, says):
    with pytest.raises(mutation.MutationError) as refused:
        call()

    assert str(refused.value) == says


def test_the_canary_says_which_mutants_lived():
    run = [_crap(KEYS[0], "survived"), _crap(KEYS[1], "no tests"), _crap(KEYS[2], "killed")]

    assert mutation.canary_problem(run) == (
        f"canary mutants of score.crap survived: src/crapkit/score.py:crap:{KEYS[0][:6]}, "
        f"src/crapkit/score.py:crap:{KEYS[1][:6]}")
    assert mutation.canary_problem([]) == "the canary score.crap was not mutated in this run"


def test_an_unjudged_run_names_every_status_it_saw():
    run = [_crap(KEYS[n], status) for n, status in enumerate(("not checked", "suspicious"))]

    assert mutation.unjudged_problem(run) == (
        "2 mutants were never judged (mutmut says not checked, suspicious): "
        f"src/crapkit/score.py:crap:{KEYS[0][:6]}, src/crapkit/score.py:crap:{KEYS[1][:6]}")


def test_a_run_with_six_unjudged_mutants_names_five():
    run = [_result("m.py", "f", f"{n:06x}".ljust(64, "0"), "not checked") for n in range(6)]

    assert mutation.unjudged_problem(run).endswith(": m.py:f:000000, m.py:f:000001, "
                                                   "m.py:f:000002, m.py:f:000003, m.py:f:000004, ...")


def test_a_verdict_with_nothing_to_say_prints_nothing():
    assert mutation.verdict_lines(mutation.Verdict()) == []


def test_a_line_after_a_function_does_not_touch_it():
    # SOURCE's a() spans lines 1-2; line 3 is the blank line after it.
    assert mutation.touched_functions(SOURCE, {3}) == []
    assert mutation.touched_functions(SOURCE, {2}) == ["a"]


def test_a_kit_module_s_glob_leaves_tests_off_the_name():
    assert mutation.mutmut_glob("tests/accuracy/kit/exact.py", "crap") == (
        "accuracy.kit.exact.x_crap__mutmut_*")


# --- the command line, parsed the way each command reads it ----------------------------------------

CPUS = mutation.os.cpu_count() or 2


@pytest.mark.parametrize("argv, expected", [
    (["weekly", "--shard", "2", "--of", "8", "--max-children", "3"],
     {"command": "weekly", "shard": 2, "of": 8, "max_children": 3}),
    (["weekly", "--shard", "1", "--of", "8"],
     {"command": "weekly", "shard": 1, "of": 8, "max_children": CPUS}),
    (["diff", "--since-weekly", "--base", "abc", "--cap-minutes", "7.5"],
     {"command": "diff", "since_weekly": True, "base": "abc", "cap_minutes": 7.5}),
    (["diff"], {"command": "diff", "since_weekly": False, "base": None, "cap_minutes": 30}),
    (["gate", "a.json", "b.json", "--update", "--no-canary"],
     {"command": "gate", "results": [Path("a.json"), Path("b.json")], "update": True,
      "no_canary": True}),
    (["gate", "a.json"],
     {"command": "gate", "results": [Path("a.json")], "update": False, "no_canary": False}),
    (["floors", "a.json"], {"command": "floors", "results": [Path("a.json")]}),
    (["covered"], {"command": "covered", "receipts": REPO / mutation.RECEIPTS}),
    (["covered", "--receipts", "d"], {"command": "covered", "receipts": Path("d")}),
    (["tools"], {"command": "tools", "max_children": CPUS}),
    (["tools", "--max-children", "4"], {"command": "tools", "max_children": 4}),
    (["key"], {"command": "key"}),
    (["killer", "tests/unit"], {"command": "killer", "pytest": ["tests/unit"]}),
])
def test_every_command_parses_to_what_its_code_reads(argv, expected):
    parsed = vars(mutation._parser().parse_args(argv))

    assert parsed == expected
    assert [type(value) for value in parsed.values()] == [type(value) for value in expected.values()]


@pytest.mark.parametrize("argv", [[], ["weekly", "--of", "8"], ["weekly", "--shard", "1"],
                                  ["gate"], ["floors"], ["nope"]])
def test_a_command_missing_what_it_needs_is_a_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as stopped:
        mutation._parser().parse_args(argv)

    assert stopped.value.code == 2
    assert capsys.readouterr().err.startswith("usage: mutation.py ")


def test_the_parser_names_the_tool_and_says_what_it_does():
    parser = mutation._parser()

    assert (parser.prog, parser.description) == (
        "mutation.py", "Mutation testing of the calculation modules, gated on a keyed survivor set.")


def test_a_machine_that_cannot_count_its_cpus_runs_two_children(monkeypatch):
    monkeypatch.setattr(mutation.os, "cpu_count", lambda: None)

    assert mutation._parser().parse_args(["tools"]).max_children == 2
    assert mutation._parser().parse_args(["weekly", "--shard", "1", "--of", "1"]).max_children == 2


# --- the environments and argv the tool hands its children ----------------------------------------

def test_the_tools_suite_runs_every_tier_at_push_settings():
    assert mutation.tools_env({"KEEP": "1", "CRAPKIT_ACCURACY_TIER": "nightly"}) == {
        "KEEP": "1", "CRAPKIT_ACCURACY_TIER": "push", "CRAPKIT_ACCURACY_COLLECT_ALL": "1",
        "PYTHONDONTWRITEBYTECODE": "1"}


def test_the_killer_argv_is_the_whole_suite_command():
    assert mutation.killer_argv(["--deselect", "x"]) == [
        sys.executable, "-m", "pytest", "tests/unit", "tests/accuracy", "-m",
        mutation.INDEPENDENT_ONLY, "-n", "4", "--dist", "worksteal", "-x", "-q", "-p",
        "no:randomly", "-p", "no:cacheprovider", "--deselect", "x"]


def test_the_killer_runs_its_argv_in_the_working_directory_with_its_env(tmp_path, monkeypatch):
    seen = {}

    def run(argv, cwd, env):
        seen.update(argv=argv, cwd=cwd, env=env)
        return mutation.subprocess.CompletedProcess(argv, 5)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mutation.subprocess, "run", run)
    monkeypatch.setenv("CRAPKIT_KILLER_PROBE", "kept")

    assert mutation.main(["killer", "-k", "x"]) == 5
    assert seen["argv"] == mutation.killer_argv(["-k", "x"])
    assert seen["cwd"].resolve() == tmp_path.resolve()
    assert seen["env"]["CRAPKIT_KILLER_PROBE"] == "kept"
    assert seen["env"]["PYTHONPATH"].split(mutation.os.pathsep)[:2] == [
        str(seen["cwd"] / "src"), str(seen["cwd"] / "tests")]


def test_captured_output_reads_a_byte_that_is_not_utf8_as_a_replacement(tmp_path):
    argv = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\xffb')"]

    assert mutation.captured(argv, tmp_path).stdout == "a\ufffdb"
    echo = [sys.executable, "-c", "import sys; sys.stdout.write(input())"]
    assert mutation.captured(echo, tmp_path, "fed").stdout == "fed"


@pytest.mark.process
def test_a_failing_git_says_the_command_and_what_git_said(tmp_path):
    with pytest.raises(mutation.MutationError) as refused:
        mutation._git(tmp_path, "rev-parse", "--verify", "no-such-ref")

    assert str(refused.value).startswith("git rev-parse --verify no-such-ref: fatal: ")


def test_a_launcher_that_leaves_mutants_undiffed_is_quoted(tmp_path):
    (tmp_path / "quiet.py").write_text("import sys\nsys.stdin.read()\nprint('x' * 600, file=sys.stderr)\n",
                                       encoding="utf-8")
    names = [f"m.x_f__mutmut_{n}" for n in range(1, 6)]

    with pytest.raises(mutation.MutationError) as refused:
        mutation._diffs(tmp_path, names, ("quiet.py",))

    assert str(refused.value) == (
        "no diff for 5 mutant(s) (m.x_f__mutmut_1, m.x_f__mutmut_2, m.x_f__mutmut_3): "
        + "x" * 500)


# --- the calc modules, the receipts and the tables a run writes -------------------------------------

def test_the_calc_modules_are_every_module_a_calcs_table_names(tmp_path):
    for packet, modules in (("p1", "src/a.py,src/b.py"), ("p2", "src/b.py, src/c.py")):
        calc = f"calc of {packet}"
        table = tmp_path / "tests" / "accuracy" / packet / "calcs.tsv"
        table.parent.mkdir(parents=True)
        table.write_text("calc\tindependent_test\tmodules\tfunctions\n"
                         f"{calc}\ttests/accuracy/{packet}/test_x.py::t\t{modules}\tsrc/a.py:f\n",
                         encoding="utf-8")

    assert mutation.calc_modules(tmp_path) == ["src/a.py", "src/b.py", "src/c.py"]


def _repo_with_a_commit(tmp_path: Path) -> tuple[Path, str]:
    (tmp_path / "repo").mkdir()
    repo = _dated_repo(tmp_path / "repo", [("2026-09-18T12:00:00Z", "a = 1\n")])
    return repo, mutation._git(repo, "rev-parse", "HEAD").strip()


@pytest.mark.process
def test_a_receipt_names_its_kind_head_and_time_and_is_written_under_receipts(tmp_path, monkeypatch):
    repo, head = _repo_with_a_commit(tmp_path)
    monkeypatch.setattr(mutation, "REPO", repo)

    receipt = mutation._receipt("tools", modules=["m.py"])
    path = mutation._write_receipt(receipt, "tools.json")

    assert {key: receipt[key] for key in ("schema", "kind", "head", "modules")} == {
        "schema": 1, "kind": "tools", "head": head, "modules": ["m.py"]}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", receipt["created"])
    assert path == repo / mutation.RECEIPTS / "tools.json"
    assert path.read_text(encoding="utf-8") == json.dumps(receipt, indent=1, sort_keys=True) + "\n"


def test_update_writes_both_tables_less_what_the_verdict_drops(tmp_path, monkeypatch):
    monkeypatch.setattr(mutation, "TABLES", tmp_path)
    kept = {**_row("src/crapkit/score.py", "crap", KEYS[1]), "reason": "r", "added": "d"}
    gone = {**_row("src/crapkit/score.py", "crap", KEYS[0]), "reason": "r", "added": "d"}
    proven = {**_row("src/crapkit/score.py", "crap", KEYS[2]), "evidence": "e", "strategy": "s",
              "checked": "d"}
    dead = {**_row("src/crapkit/score.py", "crap", KEYS[3]), "evidence": "e", "strategy": "s",
            "checked": "d"}
    verdict = mutation.Verdict(gone=(("src/crapkit/score.py", "crap", KEYS[0]),),
                               killed_equivalents=(("src/crapkit/score.py", "crap", KEYS[3]),))

    mutation._update([gone, kept], [proven, dead], verdict)

    assert mutation.read_table(tmp_path / "survivors.tsv", mutation.SURVIVOR_COLUMNS) == [kept]
    assert mutation.read_table(tmp_path / "equivalent.tsv", mutation.EQUIVALENT_COLUMNS) == [proven]


def test_gate_update_rewrites_the_tables_and_a_clean_run_exits_zero(tmp_path, monkeypatch, capsys):
    gone = {**_row("src/crapkit/score.py", "crap", KEYS[0]), "reason": "r", "added": "d"}
    _tables(tmp_path, [gone])
    monkeypatch.setattr(mutation, "TABLES", tmp_path)
    receipt = tmp_path / "r.json"
    receipt.write_text(json.dumps({"results": [_crap(KEYS[0], "killed").__dict__]}), encoding="utf-8")

    assert mutation.main(["gate", str(receipt), "--update"]) == 0
    assert mutation.read_table(tmp_path / "survivors.tsv", mutation.SURVIVOR_COLUMNS) == []
    assert "now dies" in capsys.readouterr().out


def test_gate_without_no_canary_voids_a_run_that_did_not_mutate_the_canary(tmp_path, monkeypatch,
                                                                             capsys):
    _tables(tmp_path)
    monkeypatch.setattr(mutation, "TABLES", tmp_path)
    receipt = tmp_path / "r.json"
    row = mutation.Result("m.x_f__mutmut_1", "m.py", "f", "killed").__dict__
    receipt.write_text(json.dumps({"results": [row]}), encoding="utf-8")

    assert mutation.main(["gate", str(receipt)]) == 1
    assert "void: the canary score.crap was not mutated" in capsys.readouterr().out
    assert mutation.main(["gate", str(receipt), "--no-canary"]) == 0


# --- the runs, with mutmut and the stage stood in for --------------------------------------------

class _Recorder:
    """Stands in for staged_run and records what each command handed it."""

    def __init__(self, rows, complete=True):
        self.rows, self.complete, self.calls = rows, complete, []

    def __call__(self, where, targets, globs, env, children, budget=None):
        self.calls.append({"where": where, "targets": targets, "globs": globs, "env": env,
                           "children": children, "budget": budget})
        return self.rows, self.complete


def _fake_git(monkeypatch, answers: dict | None = None) -> list:
    """Stand in for _git: record each (repo, args) and answer by the git command."""
    calls = []

    def git(repo, *args):
        calls.append((repo, list(args)))
        return (answers or {}).get(args[0], "")

    monkeypatch.setattr(mutation, "_git", git)
    return calls


def _commands_on(tmp_path, monkeypatch, rows, complete=True) -> _Recorder:
    """A repo whose HEAD is HEAD_A, empty tables and staged_run stood in for."""
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(mutation, "REPO", tmp_path / "repo")
    _fake_git(monkeypatch, {"rev-parse": HEAD_A + "\n"})
    _tables(tmp_path)
    monkeypatch.setattr(mutation, "TABLES", tmp_path)
    recorder = _Recorder(rows, complete)
    monkeypatch.setattr(mutation, "staged_run", recorder)
    return recorder


def _saved(name: str) -> dict:
    return json.loads((mutation.REPO / mutation.RECEIPTS / name).read_text(encoding="utf-8"))


def test_weekly_mutates_its_shard_and_the_canary_and_writes_its_receipt(tmp_path, monkeypatch):
    modules = ["src/crapkit/digest.py", "src/crapkit/score.py", "src/crapkit/worklist.py"]
    monkeypatch.setattr(mutation, "calc_modules", lambda: modules)
    canary = _crap(KEYS[0], "killed")
    recorder = _commands_on(tmp_path, monkeypatch, [canary])

    assert mutation.main(["weekly", "--shard", "2", "--of", "2", "--max-children", "3"]) == 0

    (call,) = recorder.calls
    assert call["where"] == mutation.CALC_STAGE
    assert call["targets"] == mutation.calc_targets(["src/crapkit/score.py"])
    assert call["globs"] == ["crapkit.score.*", "crapkit.score.x_crap__mutmut_*"]
    assert (call["env"]["CRAPKIT_ACCURACY_TIER"], call["children"], call["budget"]) == ("push", 3, None)
    saved = _saved("weekly-2.json")
    assert (saved["kind"], saved["shard"], saved["of"], saved["modules"]) == (
        "weekly", 2, 2, ["src/crapkit/score.py"])
    assert saved["results"] == [canary.__dict__]


def test_tools_mutates_every_target_here_and_writes_its_receipt(tmp_path, monkeypatch):
    targets = {"tools/accuracy/retro.py": ("t",), "tests/accuracy/kit/exact.py": ("u",)}
    asked = []
    monkeypatch.setattr(mutation, "present", lambda wanted: asked.append(wanted) or targets)
    survivor = _crap(KEYS[0], "survived")
    recorder = _commands_on(tmp_path, monkeypatch, [survivor])

    assert mutation.main(["tools", "--max-children", "5"]) == 1

    (call,) = recorder.calls
    assert (call["where"], call["targets"], call["children"]) == (mutation.TOOLS_STAGE, targets, 5)
    assert call["globs"] == ["tools.accuracy.retro.*", "accuracy.kit.exact.*"]
    assert call["env"]["CRAPKIT_ACCURACY_COLLECT_ALL"] == "1"
    saved = _saved("tools.json")
    assert (saved["kind"], saved["modules"], saved["results"]) == (
        "tools", sorted(targets), [survivor.__dict__])
    assert asked == [mutation.TOOL_TARGETS]


@pytest.mark.parametrize("complete, code", [(True, 0), (False, 1)])
def test_a_diff_run_mutates_the_changed_functions_and_says_when_its_cap_stopped_it(
        tmp_path, monkeypatch, capsys, complete, code):
    changed = [("src/crapkit/score.py", "crap")]
    monkeypatch.setattr(mutation, "calc_modules", lambda: ["src/crapkit/score.py"])
    monkeypatch.setattr(mutation, "changed_functions", lambda repo, base, modules: changed)
    recorder = _commands_on(tmp_path, monkeypatch, [_crap(KEYS[0], "killed")], complete)

    assert mutation.main(["diff", "--base", "b" * 40, "--cap-minutes", "2"]) == code

    (call,) = recorder.calls
    assert (call["where"], call["targets"], call["budget"]) == (
        mutation.CALC_STAGE, mutation.calc_targets(["src/crapkit/score.py"]), 120.0)
    assert call["globs"] == ["crapkit.score.x_crap__mutmut_*"]
    saved = _saved(f"diff-{_saved_head()[:12]}.json")
    assert (saved["base"], saved["functions"], saved["complete"]) == (
        "b" * 40, [["src/crapkit/score.py", "crap"]], complete)
    said = capsys.readouterr().out
    assert ("incomplete: the 2-minute cap stopped the run" in said) == (not complete)


def _saved_head() -> str:
    return mutation._git(mutation.REPO, "rev-parse", "HEAD").strip()


def test_a_diff_with_no_changed_function_starts_no_mutmut(monkeypatch):
    monkeypatch.setattr(mutation, "staged_run", lambda *args, **kwargs: pytest.fail("ran mutmut"))

    assert mutation._run_changed([], 60) == ([], True)


# --- staged_run over a stage whose launcher plays mutmut ------------------------------------------

STAGE_LAUNCHER = FAKE_LAUNCHER.replace(
    'if sys.argv[1:2] == ["diffs"]:',
    'if sys.argv[1:2] == ["run"] and "--sleep" in open("mode.txt").read():\n'
    '    import time; time.sleep(5)\n'
    'if sys.argv[1:2] == ["diffs"]:', 1)


def _fake_stage(tmp_path: Path, monkeypatch, mode: str = "") -> tuple[Path, list]:
    stage = _mutmut_tree(tmp_path / "stage")
    (stage / mutation.LAUNCHER_FILE).write_text(STAGE_LAUNCHER, encoding="utf-8")
    (stage / "mode.txt").write_text(mode, encoding="utf-8")
    prepared = []
    monkeypatch.setattr(mutation, "_prepare_stage",
                        lambda targets, where: prepared.append((targets, where)) or stage)
    return stage, prepared


@pytest.mark.nightly
def test_staged_run_runs_mutmut_in_the_stage_and_reruns_its_timeouts(tmp_path, monkeypatch):
    stage, prepared = _fake_stage(tmp_path, monkeypatch)

    rows, complete = mutation.staged_run(Path("w"), {"t": ()}, ["crapkit.score.*"],
                                         dict(mutation.os.environ), 3)

    assert complete and prepared == [({"t": ()}, Path("w"))]
    assert _calls(stage)[0] == ["run", "--max-children", "3", "crapkit.score.*"]
    assert ["run", "--max-children", "1", "crapkit.score.x_crap__mutmut_4"] in _calls(stage)
    assert {row.name: row.status for row in rows}["crapkit.score.x_crap__mutmut_4"] == "killed"


@pytest.mark.nightly
def test_a_run_its_budget_stopped_is_incomplete_and_reruns_nothing(tmp_path, monkeypatch):
    stage, _ = _fake_stage(tmp_path, monkeypatch, "--sleep")

    rows, complete = mutation.staged_run(Path("w"), {}, ["crapkit.score.*"],
                                         dict(mutation.os.environ), 2, budget=1)

    assert not complete
    assert [call[0] for call in _calls(stage)] == ["run", "diffs"]
    assert {row.name: row.status for row in rows}["crapkit.score.x_crap__mutmut_4"] == "timeout"


# --- the stage itself -----------------------------------------------------------------------------

@pytest.mark.process
def test_a_stage_is_a_worktree_of_head_kept_and_moved_to_each_new_head(tmp_path):
    repo, first = _repo_with_a_commit(tmp_path)
    stage = tmp_path / "deep" / "stage"

    assert mutation._stage(repo, stage) == stage
    assert mutation._git(stage, "rev-parse", "HEAD").strip() == first
    (repo / "m.py").write_text("a = 2\n", encoding="utf-8")
    mutation._git(repo, "commit", "-qam", "second")
    (stage / "mutants").mkdir()

    mutation._stage(repo, stage)

    assert mutation._git(stage, "rev-parse", "HEAD").strip() == _head_of(repo)
    assert (stage / "mutants").is_dir()


def _head_of(repo: Path) -> str:
    return mutation._git(repo, "rev-parse", "HEAD").strip()


@pytest.mark.process
def test_a_prepared_stage_holds_its_table_and_the_launcher(tmp_path, monkeypatch):
    (tmp_path / "repo").mkdir()
    repo = _dated_repo(tmp_path / "repo", [("2026-09-18T12:00:00Z", "a = 1\n")])
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n\n[tool.mutmut]\nold = 1\n',
                                         encoding="utf-8")
    mutation._git(repo, "add", "pyproject.toml")
    mutation._git(repo, "commit", "-qm", "pyproject")
    monkeypatch.setattr(mutation, "REPO", repo)

    stage = mutation._prepare_stage({"m.py": ("tests/t.py",)}, Path("stage-here"))

    assert stage == repo / "stage-here"
    table = mutation.tomllib.loads((stage / "pyproject.toml").read_text(encoding="utf-8"))
    assert table["project"] == {"name": "x"}
    assert table["tool"]["mutmut"]["source_paths"] == ["m.py"]
    assert table["tool"]["mutmut"]["also_copy"] == ["m.py", "pyproject.toml"]
    assert (stage / mutation.LAUNCHER_FILE).read_text(encoding="utf-8") == mutation.LAUNCHER


# --- what the second tools run left alive -----------------------------------------------------------
#
# Each test below pins a value the tools run at da63f7b2 showed no in-process
# test looked at. Git is stood in for, since the killer deselects every test
# that spawns it.

@pytest.mark.parametrize("body, says", [
    ("", "{path}: the header must be module function diff_sha256 reason added (tab-separated)"),
    ("module\tfunction\tdiff_sha256\treason\tadded\t\n",
     "{path}: the header must be module function diff_sha256 reason added (tab-separated)"),
    ("module\tfunction\tdiff_sha256\treason\tadded\n\nm\tf\tk\tr\td\nm\tf\n",
     "{path}:3: 2 cells, the header has 5"),
])
def test_a_table_it_cannot_read_is_refused_by_file_and_line(tmp_path, body, says):
    path = tmp_path / "survivors.tsv"
    path.write_bytes(body.encode())

    with pytest.raises(mutation.MutationError) as refused:
        mutation.read_table(path, mutation.SURVIVOR_COLUMNS)

    assert str(refused.value) == says.format(path=path)


def test_the_equivalents_come_from_their_own_table(tmp_path, monkeypatch):
    proven = {**_row("src/crapkit/score.py", "crap", KEYS[0]), "evidence": "e", "strategy": "s",
              "checked": "d"}
    _tables(tmp_path)
    mutation.write_table(tmp_path / "equivalent.tsv", mutation.EQUIVALENT_COLUMNS, [proven])
    monkeypatch.setattr(mutation, "TABLES", tmp_path)

    assert mutation._tables()[1] == [proven]


def test_five_unjudged_mutants_are_all_named_with_nothing_left_over():
    run = [_result("m.py", "f", f"{n:06x}".ljust(64, "0"), "not checked") for n in range(5)]

    assert mutation.unjudged_problem(run).endswith(": m.py:f:000000, m.py:f:000001, "
                                                   "m.py:f:000002, m.py:f:000003, m.py:f:000004")


def test_a_floor_prints_its_rate_or_that_nothing_was_mutated(capsys):
    mutation._print_floor(mutation.Floor("core", None, 95.0, 0, 0))
    mutation._print_floor(mutation.Floor("core", 50.0, 95.0, 1, 2))

    assert capsys.readouterr().out == ("mutation: floor core: not mutated, floor 95.0% ok\n"
                                       "mutation: floor core: 50.0% (1/2), floor 95.0% BELOW\n")


def test_an_exit_code_mutmut_has_no_name_for_reads_suspicious(tmp_path):
    tree = _mutmut_tree(tmp_path, {"crapkit.score.x_crap__mutmut_6": 99})

    assert mutation._meta_statuses(tree) == {"crapkit.digest.x_totals__mutmut_1": "survived",
                                             "crapkit.score.x_crap__mutmut_6": "suspicious"}


def test_a_mutant_s_module_is_found_in_the_tree_mutmut_ran_in(tmp_path):
    tree = _mutmut_tree(tmp_path)
    (tree / "src" / "crapkit" / "planted.py").write_bytes(b"x = 1\n")
    codes = {"crapkit.planted.x_f__mutmut_1": 1, "crapkit.planted.x_f__mutmut_2": 0}
    (tree / "mutants" / "src" / "crapkit" / "planted.py.meta").write_bytes(
        json.dumps({"exit_code_by_key": codes}).encode())

    rows = mutation.collect(tree, ["crapkit.planted.*"], ("fake_launch.py",))

    assert rows == [
        mutation.Result("crapkit.planted.x_f__mutmut_1", "src/crapkit/planted.py", "f", "killed", ""),
        mutation.Result("crapkit.planted.x_f__mutmut_2", "src/crapkit/planted.py", "f", "survived",
                        _key_of("crapkit.planted.x_f__mutmut_2"))]


def test_results_from_several_receipts_are_all_read(tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_bytes(json.dumps({"results": [_crap(KEYS[0], "killed").__dict__]}).encode())
    second.write_bytes(json.dumps({"results": [_crap(KEYS[1], "survived").__dict__]}).encode())

    assert mutation.load_results([first, second]) == [_crap(KEYS[0], "killed"),
                                                      _crap(KEYS[1], "survived")]


def _refusing(error: Exception):
    def command(args):
        raise error
    return command


def test_a_refusal_exits_one_and_missing_receipts_exit_three(monkeypatch, capsys):
    monkeypatch.setitem(mutation.COMMANDS, "key", _refusing(mutation.MutationError("no")))
    assert mutation.main(["key"]) == 1
    monkeypatch.setitem(mutation.COMMANDS, "key", _refusing(mutation.MissingReceipts("gone")))
    assert mutation.main(["key"]) == 3

    assert capsys.readouterr().err == "mutation.py: no\nmutation.py: gone\n"


def test_main_reads_its_command_from_the_command_line_and_key_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr(mutation.sys, "argv", ["mutation.py", "key"])
    monkeypatch.setattr(mutation.sys, "stdin", io.StringIO(SHOW_3))

    assert mutation.main() == 0
    assert capsys.readouterr().out == mutation.mutant_key(SHOW_3) + "\n"


def test_the_killer_suite_adds_no_empty_path_when_none_was_set(tmp_path):
    paths = mutation.killer_env(tmp_path, {})["PYTHONPATH"].split(mutation.os.pathsep)

    assert paths == [str(tmp_path / "src"), str(tmp_path / "tests")]


STAGED = ("[project]\nname = 'x'\n\n[tool.mutmut]\nsource_paths = ['a']\n\n"
          "[tool.pytest.ini_options]\n# [tool.mutmut] above names what mutmut mutates\nx = 1\n\n"
          "[tool.other]\ny = 2\n")


def test_the_stage_config_keeps_every_other_table_where_it_was():
    targets = {"tools/accuracy/retro.py": ("t",)}

    assert mutation.stage_config(STAGED, targets, ["src"]) == (
        "[project]\nname = 'x'\n\n[tool.pytest.ini_options]\n"
        "# [tool.mutmut] above names what mutmut mutates\nx = 1\n\n[tool.other]\ny = 2\n\n"
        + mutation._stage_table(targets, ["src"]))


def test_the_stage_table_is_exactly_what_mutmut_reads():
    table = mutation._stage_table({"b.py": ("t2", "t1"), "a.py": ("t1",)}, ["src", "tests"])

    assert table == ('[tool.mutmut]\nsource_paths = ["a.py", "b.py"]\n'
                     'pytest_add_cli_args_test_selection = ["t1", "t2"]\n'
                     f'pytest_add_cli_args = ["-p", "no:cacheprovider", "-m", "{mutation.FLOOR_SUITE}"]\n'
                     'also_copy = ["src", "tests"]\n')


def test_the_stage_copies_all_but_mutmut_s_own_folder(monkeypatch):
    calls = _fake_git(monkeypatch, {"ls-tree": "src\nmutants\nREADME.md\n"})

    assert mutation.stage_copies(Path("stage")) == ["README.md", "src"]
    assert calls == [(Path("stage"), ["ls-tree", "--name-only", "HEAD"])]


def test_a_new_stage_is_a_detached_worktree_of_head_in_a_new_folder(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch, {"rev-parse": HEAD_A + "\n"})
    stage = tmp_path / "a" / "b" / "stage"

    assert mutation._stage(tmp_path, stage) == stage

    assert stage.parent.is_dir()
    assert calls == [(tmp_path, ["rev-parse", "HEAD"]),
                     (tmp_path, ["worktree", "add", "-f", "--detach", str(stage), HEAD_A]),
                     (stage, ["checkout", "-q", "-f", "--detach", HEAD_A])]


def test_a_stage_already_there_is_moved_to_head_and_nothing_else(tmp_path, monkeypatch):
    calls = _fake_git(monkeypatch, {"rev-parse": HEAD_A})
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / ".git").write_bytes(b"gitdir: elsewhere\n")

    mutation._stage(tmp_path, stage)

    assert calls == [(tmp_path, ["rev-parse", "HEAD"]),
                     (stage, ["checkout", "-q", "-f", "--detach", HEAD_A])]


def test_the_weekly_base_asks_git_for_the_newest_first_parent_commit_before_the_run(monkeypatch):
    calls = _fake_git(monkeypatch, {"rev-list": HEAD_A + "\n"})

    assert mutation.weekly_base(Path("r"), _utc(2026, 9, 24, 12, 0)) == HEAD_A
    assert calls == [(Path("r"), ["rev-list", "-1", "--first-parent",
                                  "--before=2026-09-19T06:00:00Z", "HEAD"])]


def test_no_commit_before_the_weekly_run_is_refused_with_its_time(monkeypatch):
    _fake_git(monkeypatch, {"rev-list": "\n"})

    with pytest.raises(mutation.MutationError) as refused:
        mutation.weekly_base(Path("r"), _utc(2026, 9, 17, 12, 0))

    assert str(refused.value) == "no commit before the weekly run of 2026-09-12T06:00:00Z"


def test_changed_functions_diff_each_module_from_the_base_and_skip_what_is_gone(tmp_path, monkeypatch):
    source = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    for name in ("m.py", "n.py"):
        (tmp_path / name).write_bytes(source.encode())
    diffs = {"m.py": "@@ -2 +2 @@\n", "n.py": "@@ -6 +6 @@\n"}
    calls = []

    def git(repo, *args):
        calls.append((repo, list(args)))
        return diffs[args[-1]]

    monkeypatch.setattr(mutation, "_git", git)

    assert mutation.changed_functions(tmp_path, "base", ["gone.py", "m.py", "n.py"]) == [
        ("m.py", "a"), ("n.py", "b")]
    assert calls == [(tmp_path, ["diff", "-U0", "base", "--", "m.py"]),
                     (tmp_path, ["diff", "-U0", "base", "--", "n.py"])]


@pytest.fixture
def local_zone_five_hours_behind():
    """Local time five hours behind UTC where the platform can switch zones. Windows
    cannot, and its host zone is not UTC either on the machines that run this."""
    if not hasattr(time, "tzset"):
        yield
        return
    old = os.environ.get("TZ")
    os.environ["TZ"] = "EST+05"
    time.tzset()
    yield
    os.environ.pop("TZ")
    if old is not None:
        os.environ["TZ"] = old
    time.tzset()


def test_the_weekly_start_is_six_utc_in_any_local_zone_to_the_second(local_zone_five_hours_behind):
    now = datetime.datetime(2026, 9, 19, 7, 30, 15, 500,
                            tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))

    start = mutation.last_weekly(now)

    assert (start, start.tzinfo) == (_utc(2026, 9, 19, 6, 0), datetime.timezone.utc)


def test_a_receipt_is_stamped_in_utc_and_names_head(monkeypatch, local_zone_five_hours_behind):
    _fake_git(monkeypatch, {"rev-parse": HEAD_A + "\n"})
    before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)

    receipt = mutation._receipt("diff", base="b")

    created = datetime.datetime.strptime(receipt.pop("created"), "%Y-%m-%dT%H:%M:%SZ")
    assert before <= created.replace(tzinfo=datetime.timezone.utc) <= datetime.datetime.now(
        datetime.timezone.utc)
    assert receipt == {"schema": 1, "kind": "diff", "head": HEAD_A, "base": "b"}


def test_receipts_go_under_the_repo_s_receipt_folder_one_after_another(tmp_path, monkeypatch):
    monkeypatch.setattr(mutation, "REPO", tmp_path)

    first = mutation._write_receipt({"a": 1}, "one.json")
    second = mutation._write_receipt({"b": 2}, "two.json")

    assert first == tmp_path / mutation.RECEIPTS / "one.json"
    assert (first.read_bytes(), second.read_bytes()) == (b'{\n "a": 1\n}\n', b'{\n "b": 2\n}\n')


def _judged(monkeypatch) -> list:
    seen = []
    monkeypatch.setattr(mutation, "_judge", lambda rows, update, canary=True: seen.append(
        (rows, update, canary)) or 0)
    return seen


@pytest.mark.parametrize("argv, canary", [
    (["tools"], False), (["weekly", "--shard", "1", "--of", "1"], True), (["diff", "--base", "b"], False)])
def test_each_run_judges_its_rows_without_rewriting_the_tables(tmp_path, monkeypatch, argv, canary):
    _commands_on(tmp_path, monkeypatch, [])
    monkeypatch.setattr(mutation, "present", lambda wanted: {})
    monkeypatch.setattr(mutation, "calc_modules", lambda: [])
    monkeypatch.setattr(mutation, "changed_functions", lambda repo, base, modules: [])
    judged = _judged(monkeypatch)

    assert mutation.main(argv) == 0
    assert judged == [([], False, canary)]


def test_a_weekly_run_that_did_not_mutate_the_canary_is_void(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mutation, "calc_modules", lambda: ["src/crapkit/digest.py"])
    _commands_on(tmp_path, monkeypatch, [_result("src/crapkit/digest.py", "totals", KEYS[0], "killed")])

    assert mutation.main(["weekly", "--shard", "1", "--of", "1"]) == 1
    assert "void: the canary score.crap was not mutated" in capsys.readouterr().out


def test_a_diff_run_with_no_base_starts_from_the_last_weekly_run(tmp_path, monkeypatch):
    _commands_on(tmp_path, monkeypatch, [])
    asked, changed = [], []
    monkeypatch.setattr(mutation, "weekly_base", lambda repo, now: asked.append((repo, now)) or "w" * 40)
    monkeypatch.setattr(mutation, "calc_modules", lambda: ["src/crapkit/score.py"])
    monkeypatch.setattr(mutation, "changed_functions", lambda repo, base, modules: changed.append(
        (repo, base, modules)) or [])
    _judged(monkeypatch)

    assert mutation.main(["diff"]) == 0

    ((repo, now),) = asked
    assert (repo, now.tzinfo) == (mutation.REPO, datetime.timezone.utc)
    assert abs((datetime.datetime.now(datetime.timezone.utc) - now).total_seconds()) < 60
    assert changed == [(mutation.REPO, "w" * 40, ["src/crapkit/score.py"])]
    saved = _saved(f"diff-{HEAD_A[:12]}.json")
    assert (saved["kind"], saved["base"], saved["results"]) == ("diff", "w" * 40, [])


@pytest.mark.parametrize("cpus, children", [(None, 2), (7, 7)])
def test_a_diff_run_uses_every_cpu_or_two(monkeypatch, cpus, children):
    recorder = _Recorder([])
    monkeypatch.setattr(mutation, "staged_run", recorder)
    monkeypatch.setattr(mutation.os, "cpu_count", lambda: cpus)
    monkeypatch.setenv("CRAPKIT_ACCURACY_COLLECT_ALL", "1")

    assert mutation._run_changed([("src/crapkit/score.py", "crap")], 60) == ([], True)

    (call,) = recorder.calls
    assert (call["children"], call["budget"], call["env"]["CRAPKIT_ACCURACY_TIER"]) == (children, 60, "push")
    assert "CRAPKIT_ACCURACY_COLLECT_ALL" not in call["env"]


@pytest.mark.nightly
def test_staged_run_hands_its_environment_to_each_mutmut_run_and_keeps_to_its_globs(tmp_path,
                                                                                    monkeypatch):
    stage, _ = _fake_stage(tmp_path, monkeypatch)

    rows, complete = mutation.staged_run(Path("w"), {}, ["crapkit.score.*"],
                                         {**mutation.os.environ, "MUTATION_PROBE": "kept"}, 2)

    assert complete and (stage / "env.log").read_bytes().split() == [b"kept", b"kept"]
    assert {row.module for row in rows} == {"src/crapkit/score.py"}


# --- the stage keeps what the repo's own tests read of its table -------------------------------------
#
# tests/accuracy/kit/test_kit_contract.py reads [tool.mutmut].paths_to_mutate
# from pyproject.toml, and in the calc stage it reads the stage's copy. A stage
# table without that key failed mutmut's stats run in the accuracy image with a
# KeyError, and every mutant of the diff run stayed `not checked`. mutmut 3.8.0
# takes source_paths over paths_to_mutate, so the kept key changes nothing it
# mutates.

def test_the_stage_keeps_every_key_of_the_repo_s_table_it_does_not_set():
    targets = {"tools/accuracy/retro.py": ("t",)}

    parsed = mutation.tomllib.loads(mutation.stage_config(PYPROJECT, targets, ["src"]))

    assert parsed["tool"]["mutmut"] == {
        "paths_to_mutate": ["src/crapkit/score.py"], "source_paths": ["tools/accuracy/retro.py"],
        "pytest_add_cli_args_test_selection": ["t"],
        "pytest_add_cli_args": ["-p", "no:cacheprovider", "-m", mutation.FLOOR_SUITE],
        "also_copy": ["src"]}


def test_kept_keys_come_first_and_the_stage_s_own_keys_win():
    kept = {"also_copy": ["old"], "paths_to_mutate": ["p.py"], "debug": True}

    table = mutation._stage_table({"a.py": ("t",)}, ["src"], kept)

    assert table.splitlines()[:3] == ["[tool.mutmut]", 'paths_to_mutate = ["p.py"]', "debug = true"]
    assert table.splitlines()[-1] == 'also_copy = ["src"]'


def test_a_pyproject_with_no_mutmut_table_gets_the_stage_s_alone():
    targets = {"a.py": ("t",)}

    assert mutation.stage_config("[project]\nname = 'x'\n", targets, ["src"]) == (
        "[project]\nname = 'x'\n\n\n\n" + mutation._stage_table(targets, ["src"]))
