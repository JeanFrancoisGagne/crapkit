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
import json
import re
from pathlib import Path
import sys

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


@given(st.lists(st.text(min_size=1, max_size=5), unique=True, max_size=20),
       st.integers(1, 8))
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
