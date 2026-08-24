"""Session claims: the opt-in loop state that stops two agents picking the same
function, and the rule that lets go of one.

The store half is SQLite; the closing decision is pure, so a verify can be
argued about without running one.
"""
import sqlite3

from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore
from crapkit.worklist import closable_claims


def row(path: str, name: str, crap: float, scope: str = "core") -> ScoredRow:
    return ScoredRow(scope, path, name, 1, 11, 5, 5, 5, 9, 2, 1, 0.7, "measured", crap, "ok")


def claim(cid: int, path: str, name: str, commit: str = "c1") -> dict:
    return {"id": cid, "path": path, "long_name": name, "commit": commit,
            "created_at": "2026-01-01T00:00:00Z"}


def test_a_claim_survives_a_reopen_and_reads_back_whole(tmp_path):
    db = tmp_path / "crap.sqlite"
    SnapshotStore(db).record_claim(path="core/alpha.py", long_name="alpha( a , b )", commit="dead00")
    (held,) = SnapshotStore(db).open_claims()
    assert held["path"] == "core/alpha.py"
    assert held["long_name"] == "alpha( a , b )"
    assert held["commit"] == "dead00"
    assert held["created_at"]


def test_open_claims_are_ordered_by_id_so_two_reads_agree(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    for name in ("gamma( a , b )", "alpha( a , b )", "beta( a , b )"):
        store.record_claim(path="core/x.py", long_name=name, commit="c1")
    assert [c["long_name"] for c in store.open_claims()] == \
        ["gamma( a , b )", "alpha( a , b )", "beta( a , b )"]


def test_closing_a_claim_takes_it_out_of_the_open_set(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    first = store.record_claim(path="core/a.py", long_name="a( )", commit="c1")
    store.record_claim(path="core/b.py", long_name="b( )", commit="c1")
    assert store.close_claims([first]) == 1
    assert [c["path"] for c in store.open_claims()] == ["core/b.py"]
    assert store.close_claims([]) == 0, "closing nothing must not close everything"


def test_closing_the_same_claim_twice_closes_nothing_the_second_time(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    cid = store.record_claim(path="core/a.py", long_name="a( )", commit="c1")
    assert store.close_claims([cid]) == 1
    assert store.close_claims([cid]) == 0


def backdate(db, claim_id: int, when: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute("UPDATE attempts SET created_at = ? WHERE id = ?", (when, claim_id))
    conn.commit()
    conn.close()


def test_prune_drops_claims_older_than_the_oldest_kept_run(tmp_path):
    # A claim from a session whose runs are gone can never be closed by a verify:
    # nothing will ever score its function against the run it was taken on.
    db = tmp_path / "crap.sqlite"
    store = SnapshotStore(db)
    run_id = store.write_run(commit="c1", tool_versions={}, rows=[], kind="coverage")
    stale = store.record_claim(path="core/gone.py", long_name="gone( )", commit="c0")
    fresh = store.record_claim(path="core/here.py", long_name="here( )", commit="c1")
    backdate(db, stale, "2000-01-01T00:00:00Z")

    assert store.prune_claims({run_id}) == 1
    assert [c["id"] for c in store.open_claims()] == [fresh]


def test_prune_with_no_kept_runs_drops_nothing(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.record_claim(path="core/a.py", long_name="a( )", commit="c1")
    assert store.prune_claims(set()) == 0
    assert len(store.open_claims()) == 1


def test_the_attempts_table_lands_on_a_store_that_predates_it(tmp_path):
    # Same migration contract as the rest of the schema: opening an existing
    # database grows the table, and opening it again does not duplicate anything.
    db = tmp_path / "crap.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commit_sha TEXT NOT NULL,
            tool_versions TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        CREATE TABLE functions (
            run_id INTEGER NOT NULL,
            scope TEXT, path TEXT, long_name TEXT,
            start INTEGER, end INTEGER,
            ccn_std INTEGER, ccn_mod INTEGER, ccn INTEGER,
            nloc INTEGER, params INTEGER, nesting INTEGER
        );
    """)
    conn.commit()
    conn.close()

    SnapshotStore(db).record_claim(path="core/a.py", long_name="a( )", commit="c1")
    assert len(SnapshotStore(db).open_claims()) == 1


def test_a_function_back_under_its_ceiling_releases_its_claim():
    claims = [claim(1, "core/alpha.py", "alpha( a , b )"),
              claim(2, "core/beta.py", "beta( a , b )")]
    # crap 5.675 = 5^2 * 0.3^3 + 5, under the target of 6; 30.0 = 5^2 + 5 is not
    fresh = [row("core/alpha.py", "alpha( a , b )", 5.675),
             row("core/beta.py", "beta( a , b )", 30.0)]
    assert closable_claims(claims, fresh, target=6, scope_targets={}, stale_commits=set()) == [1]


def test_the_ceiling_is_the_functions_own_scope_ceiling():
    claims = [claim(7, "extra/delta.py", "delta( a , b )")]
    fresh = [row("extra/delta.py", "delta( a , b )", 5.675, scope="extra")]
    assert closable_claims(claims, fresh, target=6, scope_targets={"extra": 4},
                           stale_commits=set()) == []
    assert closable_claims(claims, fresh, target=4, scope_targets={"extra": 6},
                           stale_commits=set()) == [7]


def test_a_claim_taken_on_a_commit_that_left_history_is_released():
    claims = [claim(3, "core/alpha.py", "alpha( a , b )", commit="rewritten")]
    fresh = [row("core/alpha.py", "alpha( a , b )", 72.0)]
    assert closable_claims(claims, fresh, target=6, scope_targets={},
                           stale_commits={"rewritten"}) == [3]


def test_a_claim_on_a_function_the_run_never_scored_stays_open():
    claims = [claim(4, "core/ghost.py", "ghost( )")]
    fresh = [row("core/alpha.py", "alpha( a , b )", 5.675)]
    assert closable_claims(claims, fresh, target=6, scope_targets={}, stale_commits=set()) == []


def test_closable_ids_come_back_sorted_whatever_order_the_claims_arrive_in():
    claims = [claim(9, "core/c.py", "c( )"), claim(2, "core/a.py", "a( )"),
              claim(5, "core/b.py", "b( )")]
    fresh = [row("core/a.py", "a( )", 5.0), row("core/b.py", "b( )", 5.0),
             row("core/c.py", "c( )", 5.0)]
    assert closable_claims(claims, fresh, target=6, scope_targets={},
                           stale_commits=set()) == [2, 5, 9]
