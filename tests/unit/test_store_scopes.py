"""Scope filtering pushed into the SQL read: the worklist surface cuts by scope
without the caller reading the rows it is about to throw away."""
from crapkit.score import ScoredRow
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore

INV = [
    InventoryRow("py", "pylib/mod.py", "guarded( a b )", 1, 9, 5, 5, 5, 8, 2, 2),
    InventoryRow("src", "src/app.ts", "dispatch( x )", 3, 20, 9, 4, 4, 15, 1, 1),
    InventoryRow("ui", "ui/panel.ts", "render( )", 1, 40, 12, 12, 12, 30, 0, 3),
]

SCORED = [
    ScoredRow("py", "pylib/mod.py", "guarded( a b )", 1, 9, 5, 5, 5, 8, 2, 2,
              0.0, "untested", 30.0, "add-tests"),
    ScoredRow("src", "src/app.ts", "dispatch( x )", 3, 20, 9, 4, 4, 15, 1, 1,
              0.75, "measured", 4.25, "ok"),
    ScoredRow("ui", "ui/panel.ts", "render( )", 1, 40, 12, 12, 12, 30, 0, 3,
              0.0, "untested", 156.0, "split"),
]


def seeded(tmp_path) -> tuple[SnapshotStore, int]:
    store = SnapshotStore(tmp_path / "crap.sqlite")
    return store, store.write_run(commit="c1", tool_versions={}, rows=SCORED,
                                  lanes={"unit": {}}, kind="coverage")


def test_inventory_read_keeps_only_the_named_scopes(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="c1", tool_versions={}, rows=INV)
    assert store.read_rows(run_id, scopes=["py"]) == [INV[0]]
    assert store.read_rows(run_id, scopes=["ui", "py"]) == [INV[0], INV[2]]


def test_no_scopes_named_reads_every_scope(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="c1", tool_versions={}, rows=INV)
    assert store.read_rows(run_id, scopes=None) == INV
    assert store.read_rows(run_id, scopes=[]) == INV


def test_a_scope_nobody_declared_reads_empty_rather_than_everything(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="c1", tool_versions={}, rows=INV)
    assert store.read_rows(run_id, scopes=["nope"]) == []


def test_scored_read_filters_by_scope_alongside_the_ccn_floor(tmp_path):
    store, run_id = seeded(tmp_path)
    assert store.read_scored(run_id, scopes=["src"]) == [SCORED[1]]
    assert store.read_scored(run_id, min_ccn=5, scopes=["src", "py"]) == [SCORED[0]]


def test_below_floor_count_is_scoped_too_or_an_empty_queue_lies(tmp_path):
    # ccn is 5 / 4 / 12; a floor of 5 leaves only src/app.ts behind, so a
    # py-scoped queue must report 0 skipped, not the repo-wide 1.
    store, run_id = seeded(tmp_path)
    assert store.count_scored_below(run_id, 5) == 1
    assert store.count_scored_below(run_id, 5, scopes=["py"]) == 0
    assert store.count_scored_below(run_id, 5, scopes=["src"]) == 1


def test_scope_names_are_bound_as_parameters_not_pasted_in(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="c1", tool_versions={}, rows=INV)
    assert store.read_rows(run_id, scopes=["'); DROP TABLE functions; --"]) == []
    assert store.read_rows(run_id) == INV
