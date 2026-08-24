"""The per-scope CRAP ceiling, and when it is not per-scope at all.

`trend` sums three numbers per run inside the scan, and the over-target sum
compares every row against its scope's ceiling. A config that declares scopes
but gives none of them a target of their own is the common case, and it built a
CASE that says the same number in every branch — over 1.12 M rows of history,
twice.
"""
from crapkit.digest import totals
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

SCOPES = ("api", "ui")


def scored(n: int) -> list:
    return [ScoredRow(SCOPES[i % 2], f"src/m{i % 3}.py", f"f{i % 5}( a )",
                      1 + i, 9 + i, 3 + i % 9, 3 + i % 9, 3 + i % 9, 5, 1, 1,
                      0.25, "measured", 2.0 + i * 1.5, "add-tests", 0)
            for i in range(n)]


def traced(store, call) -> list[str]:
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        call()
    finally:
        store._conn.set_trace_callback(None)
    return seen


def reads(store, call) -> list[str]:
    return [s for s in traced(store, call) if s.lstrip().upper().startswith("SELECT")]


def seeded(tmp_path) -> tuple[SnapshotStore, int]:
    store = SnapshotStore(tmp_path / "crap.sqlite")
    return store, store.write_run(commit="c1", tool_versions={}, rows=scored(60),
                                  lanes={"unit": {}})


def test_scope_targets_that_all_match_the_repo_target_build_no_case(tmp_path):
    store, _ = seeded(tmp_path)
    same = {"api": 6, "ui": 6}

    for sql in reads(store, lambda: store.run_totals(target=6, scope_targets=same)):
        assert "CASE" not in sql, f"a CASE whose every branch says 6: {sql}"


def test_a_redundant_scope_target_gives_the_same_totals(tmp_path):
    store, _ = seeded(tmp_path)

    assert store.run_totals(target=6, scope_targets={"api": 6, "ui": 6}) == \
        store.run_totals(target=6)
    assert store.run_scope_totals(target=6, scope_targets={"api": 6, "ui": 6}) == \
        store.run_scope_totals(target=6)


def test_a_scope_with_its_own_target_still_gets_its_branch(tmp_path):
    store, _ = seeded(tmp_path)

    (sql, *_) = reads(store, lambda: store.run_totals(target=6, scope_targets={"ui": 4, "api": 6}))

    assert "CASE" in sql, "a scope that really differs still needs its branch"
    assert sql.count("WHEN") == 1, f"only the scope that differs belongs in the CASE: {sql}"


def test_one_ceiling_for_the_repo_stops_joining_the_identities(tmp_path):
    """The whole-run totals join identities for one reason: the scope the CASE
    switches on. With one number for the repo there is nothing to look up, and
    the join is an index seek per row that changes no answer."""
    store, _ = seeded(tmp_path)

    for sql in reads(store, lambda: store.run_totals(target=6, scope_targets={"api": 6})):
        assert "identities" not in sql, f"a join nothing in the query reads: {sql}"


def test_a_scope_with_its_own_ceiling_still_joins_for_it(tmp_path):
    store, _ = seeded(tmp_path)

    (sql, *_) = reads(store, lambda: store.run_totals(target=6, scope_targets={"ui": 4}))

    assert "JOIN identities" in sql, "the CASE reads i.scope, so the scope has to be there"


def test_the_per_scope_totals_always_join(tmp_path):
    """run_scope_totals groups BY the scope, so it needs the identity whatever
    the ceiling looks like."""
    store, _ = seeded(tmp_path)

    (sql, *_) = reads(store, lambda: store.run_scope_totals(target=6, scope_targets={"api": 6}))

    assert "JOIN identities" in sql


def test_the_collapsed_ceiling_counts_what_the_digest_counts(tmp_path):
    """The one thing this may never change: over-target decided in SQL and
    over-target decided over the rows in hand have to agree."""
    store, run_id = seeded(tmp_path)
    rows = store.read_scored(run_id)

    for scope_targets in ({"api": 6, "ui": 6}, {"ui": 4}, None):
        (_n, over, _load) = store.run_totals(target=6, scope_targets=scope_targets)[run_id]
        assert over == totals(rows, target=6, scope_targets=scope_targets).over_target, \
            scope_targets


def test_per_scope_totals_agree_with_the_whole_run_totals(tmp_path):
    store, run_id = seeded(tmp_path)
    same = {"api": 6, "ui": 6}

    whole = store.run_totals(target=6, scope_targets=same)[run_id]
    by_scope = store.run_scope_totals(target=6, scope_targets=same)[run_id]

    assert sum(n for n, _o, _l in by_scope.values()) == whole[0]
    assert sum(o for _n, o, _l in by_scope.values()) == whole[1]
