"""store at its edges: the literal pieces its SQL is built from, and the answers at each boundary.

The select list, the rollup key and the identity filter are pinned word for
word. A 16-field scored row from before occurrence stores its own verdicts, an
unknown flag included, and reads back unmarked. A trusted run that scored
nothing totals zero, and history totals publish under the ceiling they were
asked for. A legacy claim is held by its handle or its key name, an old
expression claim by no precise key, and a collision in one run never refuses a
read of another.
"""
import sqlite3
from collections import namedtuple

import pytest

from crapkit import store
from crapkit.errors import ToolError
from crapkit.score import ScoredRow, crap_load
from crapkit.store import SnapshotStore

FAILED = {"id": 1, "kind": "verify", "verdict_ok": False, "lanes": {}}
COVERAGE = {"id": 2, "kind": "coverage", "verdict_ok": None, "lanes": {}}
# A scored row written before occurrence and inline_body existed: 16 fields.
Legacy = namedtuple("Legacy", ScoredRow._fields[:16])


def scored(name: str, start: int, occurrence: int, *, crap: float = 5.0, scope: str = "s") -> ScoredRow:
    return ScoredRow(scope, "a.py", name, start, start + 2, 2, 2, 2, 3, 0, 0, 0.5, "measured", crap, "ok",
                     0, occurrence, 0)


def opened(tmp_path, *runs) -> SnapshotStore:
    snapshot = SnapshotStore(tmp_path / "store.db")
    for rows in runs:
        snapshot.write_run(commit="c", tool_versions={}, rows=list(rows))
    return snapshot


def raw(snapshot: SnapshotStore, sql: str, rows) -> None:
    snapshot._conn.executemany(sql, rows)
    snapshot._conn.commit()


def test_the_literal_pieces_of_the_sql_are_word_for_word():
    assert store._selected(flag="fl.id", remedy="rm.id") == (
        "f.start, f.end, f.ccn_std, f.ccn_mod, f.ccn, f.nloc, f.params, f.nesting, f.cov, fl.id, "
        "f.crap, rm.id, f.cognitive, f.occurrence, f.inline_body")
    assert store._ceiling_key(30, {"b": 10, "a": 20, "c": 30}) == '[3,30,[["a",20],["b",10]]]'
    assert store._identity_where(None, None, None) == ("1", [])


def test_the_small_helpers_answer_at_their_empty_ends():
    assert store._code({}, None) is None
    assert store._inflate('{"a": 1}') == '{"a": 1}'
    assert store._newest_non_hook_id([{"id": 1, "kind": "hook"}]) == set()
    assert store._baseline_keep_ids([FAILED]) == {1}
    assert store.outstanding_failure([FAILED, COVERAGE]) == FAILED


def test_a_sixteen_field_scored_row_stores_its_own_verdicts_and_reads_back_unmarked(tmp_path):
    row = Legacy("s", "a.py", "f", 1, 3, 2, 2, 2, 3, 0, 0, 0.5, "novel", 5.0, "ok", 4)
    snapshot = opened(tmp_path, [row])

    assert snapshot.read_scored(1) == [ScoredRow(*row, 0, 0)]


def test_a_trusted_run_that_scored_nothing_totals_zero_under_the_ceiling_asked_for(tmp_path):
    snapshot = opened(tmp_path, [], [scored("f", 1, 1, crap=20.0)])

    totals = snapshot.history_totals(target=30, scope_targets={"s": 10})

    load = crap_load([20.0])
    assert [total[1:] for total in totals] == [((0, 0, 0.0), {}), ((1, 1, load), {"s": (1, 1, load)})]
    assert snapshot._unrolled(store._ceiling_key(30, {"s": 10})) == []
    assert snapshot.prior_scored_run(commit="c", before=2) == 1


def test_a_verdict_stamped_with_no_count_carries_zero_findings(tmp_path):
    snapshot = opened(tmp_path, [])

    snapshot.set_verdict_ok(1, True)

    assert [(run["verdict_ok"], run["findings"]) for run in snapshot.list_runs()] == [(True, 0)]


def test_the_write_paths_refuse_a_gone_run_and_close_several_claims_at_once(tmp_path):
    snapshot = opened(tmp_path)
    first = snapshot.record_claim(path="a.py", long_name="f", commit="c")
    second = snapshot.record_claim(path="a.py", long_name="g", commit="c")

    with pytest.raises(ToolError) as caught:
        snapshot.write_overrides(9, [])

    assert str(caught.value) == "override run 9 no longer exists; rerun before granting debt"
    assert snapshot.close_claims([first, second]) == 2
    assert SnapshotStore(":memory:").size_bytes() == 0


def test_a_legacy_claim_is_held_by_its_handle_or_its_key_name_and_no_more(tmp_path):
    snapshot = opened(tmp_path)
    raw(snapshot, "INSERT INTO attempts (path, long_name, commit_sha, handle, key_name, key_version) "
                  "VALUES (?, ?, 'c', ?, ?, 1)", [("a.py", "f", "f#2", None), ("b.py", "g", None, "g#2")])

    assert snapshot.attempts_for([("a.py", "f#3"), ("b.py", "g#3")]) == {("a.py", "f#3"): [],
                                                                        ("b.py", "g#3"): []}
    assert snapshot.record_claim(path="a.py", long_name="f", commit="c", handle="f#3") is not None


def test_an_old_expression_claim_keeps_no_precise_key_and_a_plain_one_is_version_one(tmp_path):
    snapshot = opened(tmp_path)

    snapshot.record_claim(path="c.ts", long_name="(anonymous)", commit="c", key_name="(anonymous)#2")
    snapshot.record_claim(path="a.py", long_name="f", commit="c")

    assert [(claim["key_name"], claim["key_version"]) for claim in snapshot.open_claims()] == [
        (None, 0), (None, 1)]


def test_positioned_twins_are_a_collision_group_and_one_runs_legacy_twins_refuse_no_other_run(tmp_path):
    legacy = [scored("g", 5, 0), scored("g", 5, 0)]
    snapshot = opened(tmp_path, legacy, [scored("f", 1, 1), scored("f", 1, 2)])

    assert snapshot.historical_collision_groups() == {("a.py", "g"), ("a.py", "f")}
    assert snapshot.historical_collision_groups(paths={"a.py"}) == {("a.py", "g"), ("a.py", "f")}
    assert list(snapshot.read_marks(2).verdicts) == [("a.py", "f", 1, 1), ("a.py", "f", 1, 2)]


def test_the_newest_run_holding_a_group_is_its_witness(tmp_path, monkeypatch):
    snapshot = opened(tmp_path)
    monkeypatch.setattr(snapshot, "_collisions", lambda run_id=None: [("a.py", "f", 3, 0), ("a.py", "f", 1, 0)])

    assert snapshot.identity_witness_run_ids() == {3}


def test_a_prune_deletes_only_the_runs_its_selection_observed(tmp_path):
    snapshot = opened(tmp_path, [], [], [])

    assert snapshot.prune_runs({1}, observed_ids={2}) == 1
    assert [run["id"] for run in snapshot.list_runs()] == [1, 3]


def test_a_store_from_before_coverage_gains_integer_verdict_columns_and_reads_as_current(tmp_path):
    old = store._SCHEMA.replace("    cov REAL, flag INTEGER, crap REAL, remedy INTEGER,\n", "")
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.executescript(old)
    conn.close()

    snapshot = SnapshotStore(tmp_path / "old.db")

    assert "flag INTEGER" not in old
    assert (snapshot._declared_types("functions")["flag"], snapshot._current()) == ("INTEGER", True)


def test_a_lane_record_an_older_writer_left_as_text_is_deflated_on_the_next_open(tmp_path):
    snapshot = opened(tmp_path, [])
    raw(snapshot, "UPDATE runs SET lanes = ?", [('{"u": 1}',)])
    snapshot.close()

    reopened = SnapshotStore(tmp_path / "store.db")

    assert reopened._conn.execute("SELECT typeof(lanes) FROM runs").fetchall() == [("blob",)]
    assert reopened.list_runs()[0]["lanes"] == {"u": 1}


def test_a_store_owing_the_rekey_drops_the_dead_indexes_first(tmp_path, monkeypatch):
    snapshot = opened(tmp_path)
    monkeypatch.setattr(snapshot, "_identity_key", lambda: ())

    assert snapshot._restack_steps() == ("DROP INDEX IF EXISTS idx_functions_run_path",
                                         "DROP INDEX IF EXISTS idx_identities_path",
                                         *store._REKEY_IDENTITIES)
