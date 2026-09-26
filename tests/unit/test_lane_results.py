"""lane_results over the runs the store gives back, not over hand-built dicts.

Each shape below is written through `SnapshotStore.write_run` and read back
with `list_runs`, the way verify and coverage read them, then handed to
lane_results. A lane records a count and a failure list only when it parsed a
junit report; every reader here must read that absence as None, never as 0
tests or no failures, and walk back to the newest run that recorded one.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from crapkit.lane_results import (LaneResults, baseline_failures, counted_record,
                                  failure_ids, portable_results, recorded_failures, results_of,
                                  suite_drops, without_results)
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore, trusted_runs

ROWS = [InventoryRow("src", "src/a.py", "hot( n )", 1, 9, 7, 5, 5, 8, 1, 2)]

# name -> (kind, crapkit version that wrote it, the lane's provenance)
SHAPES = {
    "junit-parsed": ("coverage", "0.8.1", {"tests_total": 20, "tests_skipped": 1,
                                             "failures": ["t::a"], "exit_code": 1}),
    "junit-gone-under-reuse": ("coverage", "0.8.1", {"scopes": ["src"]}),
    "no-results-artifact": ("coverage", "0.8.1", {"exit_code": 1}),
    "verify-with-a-retried-pass": ("verify", "0.8.1", {"tests_total": 20,
                                                       "failures": ["t::a", "t::b"],
                                                       "retried_passes": ["t::b"]}),
    "verify-written-by-0.7.6": ("verify", "0.7.6", {"tests_total": 20,
                                                    "failures": ["t::a", "t::b"]}),
    "verify-by-a-dev-build": ("verify", "dev", {"tests_total": 20, "failures": ["t::a"]}),
}

# name -> the record lane_results reads off it
EXPECTED = {
    "junit-parsed": LaneResults(20, 1, frozenset({"t::a"}), frozenset()),
    "junit-gone-under-reuse": LaneResults(None, None, None, frozenset()),
    "no-results-artifact": LaneResults(None, None, None, frozenset()),
    "verify-with-a-retried-pass": LaneResults(20, None, frozenset({"t::a", "t::b"}),
                                              frozenset({"t::b"})),
    "verify-written-by-0.7.6": LaneResults(20, None, None, frozenset()),
    "verify-by-a-dev-build": LaneResults(20, None, frozenset({"t::a"}), frozenset()),
}


def stored(tmp_path: Path, *names: str) -> list[dict]:
    """The runs, oldest first, as `list_runs` gives them back."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    with closing(store._conn):
        for name in names:
            kind, version, prov = SHAPES[name]
            store.write_run(commit="a" * 40, tool_versions={"crapkit": version}, rows=ROWS,
                            lanes={"py": prov}, kind=kind)
            if kind == "verify":
                store.set_verdict_ok(store.list_runs()[-1]["id"], True)
        return store.list_runs()


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_stored_lane_record_reads_absence_as_none(tmp_path, shape):
    (run,) = stored(tmp_path, shape)

    assert results_of(run, "py") == EXPECTED[shape]
    assert results_of(run, "renamed") == LaneResults(None, None, None, frozenset())


def test_a_failure_that_passed_its_retry_is_not_carried(tmp_path):
    (run,) = stored(tmp_path, "verify-with-a-retried-pass")

    assert results_of(run, "py").carried == {"t::a"}


@pytest.mark.parametrize(("older", "carried", "borrowed"), [
    pytest.param("junit-parsed", {"t::a"}, True, id="older-run-recorded-a-list"),
    pytest.param("verify-written-by-0.7.6", set(), False, id="older-list-is-untrusted"),
    pytest.param("no-results-artifact", set(), False, id="no-run-recorded-a-list"),
])
def test_a_baseline_with_no_list_forgives_from_the_newest_run_behind_it(
        tmp_path, older, carried, borrowed):
    older_run, baseline = stored(tmp_path, older, "junit-gone-under-reuse")

    found = baseline_failures(baseline, {"py": {"failures": ["t::a"]}}, lambda: [older_run])

    assert set(found.carried) == carried
    assert ("py" in found.borrowed) is borrowed
    assert found.unrecorded == (() if borrowed else ("py",))


def test_the_count_comes_from_the_newest_run_behind_that_recorded_one(tmp_path):
    counted, gone, baseline = stored(tmp_path, "junit-parsed", "junit-gone-under-reuse",
                                     "no-results-artifact")

    assert counted_record(baseline, lambda: [gone, counted], "py") is counted
    assert counted_record(counted, lambda: [], "py") is counted
    assert counted_record(baseline, lambda: [gone], "py") is None


# --- coverage's suite drop is the second caller of the same walk ---------------

def test_coverage_compares_with_the_newest_trusted_run_that_counted_the_lane(tmp_path):
    stored(tmp_path, "junit-parsed", "junit-gone-under-reuse")
    store = SnapshotStore(tmp_path / "crap.sqlite")
    with closing(store._conn):
        runs = trusted_runs(store)

    (note,) = suite_drops(lambda: reversed(runs), {"py": {"tests_total": 12}})

    assert note.startswith(f"lane 'py' ran 12 tests, 8 fewer than run {runs[0]['id']}'s 20 "
                           f"(the last trusted run, run {runs[1]['id']}, recorded no test "
                           "count for it)")


def test_the_lanes_page_quotes_both_drop_lines_coverage_prints():
    root = Path(__file__).resolve().parents[2]
    page = (root / "docs" / "lanes.md").read_text(encoding="utf-8").splitlines()
    runs = [{"id": 2, "kind": "coverage", "lanes": {"py": {}}},
            {"id": 1, "kind": "coverage", "lanes": {"py": {"tests_total": 20}}}]

    for behind in (runs, runs[1:]):
        (note,) = suite_drops(lambda: behind, {"py": {"tests_total": 12}})
        assert f"crapkit: {note}" in page, note


def test_a_drop_from_the_last_trusted_run_says_so_without_a_run_id(tmp_path):
    stored(tmp_path, "junit-parsed")
    store = SnapshotStore(tmp_path / "crap.sqlite")
    with closing(store._conn):
        runs = trusted_runs(store)

    (note,) = suite_drops(lambda: reversed(runs), {"py": {"tests_total": 12}})

    assert note == ("lane 'py' ran 12 tests, 8 fewer than the last trusted run's 20 - check "
                    "the runner's log for a worker that died without reporting it")


@pytest.mark.parametrize("current", [{"py": {"exit_code": 0}}, {"py": {}}, {}],
                         ids=["no-results-artifact", "junit-gone-under-reuse", "lane-left-out"])
def test_a_run_that_counted_nothing_reads_no_history(current):
    """The store is read only when a lane has a count to compare."""
    def behind():
        raise AssertionError("no lane counted tests, so no history is read")

    assert suite_drops(behind, current) == []


def test_a_stored_count_of_zero_is_a_count_not_an_absence():
    """Both readers keep a lane whose count is 0: one rule decides what a count
    is, where coverage once passed over a 0 that verify kept."""
    runs = [{"kind": "coverage", "lanes": {"py": {"tests_total": 0}}},
            {"kind": "coverage", "lanes": {"py": {"tests_total": 20}}}]

    assert suite_drops(lambda: runs, {"py": {"tests_total": 12}}) == []
    assert counted_record(runs[0], lambda: runs[1:], "py") is runs[0]


# --- what a baseline file carries ------------------------------------------------

@pytest.mark.parametrize(("shape", "carried"), [
    ("junit-parsed", {"tests_total": 20, "tests_skipped": 1, "failures": ["t::a"]}),
    ("junit-gone-under-reuse", None),
    ("verify-with-a-retried-pass", {"tests_total": 20, "failures": ["t::a", "t::b"],
                                    "retried_passes": ["t::b"]}),
    ("verify-written-by-0.7.6", {"tests_total": 20}),
])
def test_a_baseline_file_carries_only_what_the_run_recorded(tmp_path, shape, carried):
    (run,) = stored(tmp_path, shape)

    assert portable_results(run).get("py") == carried


def test_without_results_names_the_lanes_that_recorded_no_list():
    provenance = {"a": {"failures": []}, "b": {"exit_code": 1}, "c": {"tests_total": 3}}

    assert without_results(provenance) == ["b", "c"]


# --- one reader: no module outside lane_results reads a lane's result fields -----

import re  # noqa: E402

_FIELD = r"""["'](failures|tests_total|tests_skipped)["']"""
_RAW_READ = re.compile(rf"(\.get\(|\[){_FIELD}|{_FIELD} in ")
_ROOT = Path(__file__).resolve().parents[2]


def _python_files() -> list[Path]:
    """Every module that could read a lane record: the package and the repo's
    tools, whose release guard reads the verify run it publishes on."""
    return [path for top in ("src/crapkit", "tools") for path in sorted((_ROOT / top).rglob("*.py"))
            if path.name != "lane_results.py"]


def test_no_module_but_lane_results_reads_a_lanes_result_fields():
    """Each raw read applied the absent-means-None rule by hand, and one that
    wrote `.get("tests_total", 0)` reported a lane that ran nothing as every test
    short. `read_results` is the one place that rule lives."""
    raw = [f"{path.relative_to(_ROOT).as_posix()}:{number}"
           for path in _python_files()
           for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
           if _RAW_READ.search(line)]

    assert raw == []


@pytest.mark.parametrize("line", [
    'lane.get("failures")', "lane.get('tests_total')", "prov['tests_skipped']",
    '"failures" in lane', "'tests_total' in prov",
], ids=["get-double", "get-single", "index-single", "in-double", "in-single"])
def test_the_one_reader_guard_sees_either_quote(line):
    assert _RAW_READ.search(line)


@pytest.mark.parametrize("prov, expected", [
    ({"tests_total": True, "tests_skipped": False}, LaneResults(None, None, None, frozenset())),
    ({"tests_total": "2", "tests_skipped": "0"}, LaneResults(None, None, None, frozenset())),
    ({"tests_total": -1, "tests_skipped": 2.0}, LaneResults(None, None, None, frozenset())),
    ({"tests_total": 0, "tests_skipped": 0}, LaneResults(0, 0, None, frozenset())),
    ({"failures": {}}, LaneResults(None, None, None, frozenset())),
    ({"failures": "t::a"}, LaneResults(None, None, None, frozenset())),
    ({"failures": [1]}, LaneResults(None, None, None, frozenset())),
    ({"failures": [], "retried_passes": "t::a"}, LaneResults(None, None, frozenset(), frozenset())),
], ids=["bools", "strings", "negative-and-float", "zero-is-counted", "failures-a-dict",
        "failures-a-string", "failures-not-ids", "retried-a-string"])
def test_a_field_of_the_wrong_type_reads_as_not_recorded(prov, expected):
    """A baseline file is JSON anyone can edit, and the release guard reads the
    ledger's JSON: a count is an int of 0 or more and a list names test ids, and
    any other value is no record at all, never a count or a list to compare."""
    from crapkit.lane_results import read_results

    assert read_results(prov) == expected


@pytest.mark.parametrize("provenance, expected", [
    ({"py": {"failures": ["t::a"], "tests_total": 2}}, {"t::a"}),
    ({"py": {"failures": [], "tests_total": 2}}, set()),
    ({"py": {"exit_code": 0}}, set()),
    ({}, set()),
], ids=["listed", "empty-list", "no-list", "lane-left-out"])
def test_a_lane_that_recorded_no_list_contributes_no_failure(provenance, expected):
    assert recorded_failures(provenance, "py") == expected
    assert failure_ids(provenance) == expected


def test_failure_ids_holds_every_lanes_list():
    provenance = {"py": {"failures": ["t::a"]}, "js": {"failures": ["s::b"]}, "go": {}}

    assert failure_ids(provenance) == {"t::a", "s::b"}


# --- the interface: the names callers use, imported once --------------------------

import ast  # noqa: E402

_MODULE = _ROOT / "src" / "crapkit" / "lane_results.py"


def _public_names(tree: ast.Module) -> set[str]:
    """The top-level functions, classes and constants a caller could import."""
    names = {node.name for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    names |= {target.id for node in tree.body if isinstance(node, ast.Assign)
              for target in node.targets if isinstance(target, ast.Name)}
    return {name for name in names if not name.startswith("_")}


def _returned_records(tree: ast.Module) -> set[str]:
    """The names public functions return: a record type is interface without an importer."""
    return {ast.unparse(node.returns) for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_") and node.returns}


def test_every_public_lane_results_name_has_a_caller_outside_it():
    """arch-15 narrows lane_results to the names its callers use. A name nobody
    outside the module reads is its own detail and stays private; a record a
    public function returns is interface whoever names it."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    outside = "\n".join(path.read_text(encoding="utf-8") for path in _python_files())
    unused = sorted(name for name in _public_names(tree) - _returned_records(tree)
                    if not re.search(rf"\b{name}\b", outside))

    assert unused == []


def test_verifying_imports_lane_results_once_at_module_level():
    """cli/verifying read lane_results through a local import in each of eleven
    functions; one module import lists what verify reads in one place."""
    path = _ROOT / "src" / "crapkit" / "cli" / "verifying.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
               and (node.module == "lane_results"
                    or any(alias.name == "lane_results" for alias in node.names))]

    assert len(imports) == 1 and imports[0] in tree.body


@pytest.mark.parametrize(("shape", "distrusted"), [
    ("verify-written-by-0.7.6", True),
    ("verify-with-a-retried-pass", False),
    ("verify-by-a-dev-build", False),
    ("junit-parsed", False),
    ("no-results-artifact", False),
])
def test_a_list_a_baseline_holds_but_cannot_forgive_from(tmp_path, shape, distrusted):
    """verify says why it passed over a baseline's own failure list: the run
    held one written before retried passes were named apart, or it held none."""
    from crapkit.lane_results import distrusted_list

    (run,) = stored(tmp_path, shape)

    assert distrusted_list(run, "py") is distrusted
    assert distrusted_list(run, "renamed") is False
