"""Store seam: rows + run metadata in, identical rows and queryable runs out. SQLite on disk."""
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore


def rows():
    return [
        InventoryRow("src", "src/a.ts", "f( x )", 1, 9, 7, 5, 5, 8, 1, 2),
        InventoryRow("src", "src/b.ts", "g( )", 3, 20, 12, 12, 12, 15, 0, 4),
    ]


def test_round_trip_returns_identical_rows(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc123", tool_versions={"lizard": "1.24.0"}, rows=rows())
    assert store.read_rows(run_id) == rows()


def test_runs_are_append_only_and_carry_metadata_separately(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    first = store.write_run(commit="abc123", tool_versions={"lizard": "1.24.0"}, rows=rows())
    second = store.write_run(commit="abc123", tool_versions={"lizard": "1.24.0"}, rows=rows())
    assert first != second
    runs = store.list_runs()
    assert [r["commit"] for r in runs] == ["abc123", "abc123"]
    assert all(r["tool_versions"]["lizard"] == "1.24.0" for r in runs)
    assert store.read_rows(first) == store.read_rows(second)


def test_latest_run_for_commit(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.write_run(commit="old", tool_versions={}, rows=rows())
    newest = store.write_run(commit="new", tool_versions={}, rows=rows()[:1])
    assert store.latest_run(commit="new") == newest
    assert store.latest_run(commit="missing") is None


def test_scored_rows_round_trip_with_lane_provenance(tmp_path):
    from crapkit.score import ScoredRow
    store = SnapshotStore(tmp_path / "crap.sqlite")
    scored = [ScoredRow("src", "src/a.ts", "f( )", 1, 9, 7, 5, 5, 8, 1, 2, 0.75, "measured", 5.78125, "ok")]
    run_id = store.write_run(commit="abc", tool_versions={}, rows=scored,
                             lanes={"unit": {"artifact_sha256": "d0ff", "exit_code": 0}})
    assert store.read_scored(run_id) == scored
    run = [r for r in store.list_runs() if r["id"] == run_id][0]
    assert run["lanes"]["unit"]["artifact_sha256"] == "d0ff"


def test_inventory_runs_still_round_trip_in_the_extended_schema(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc", tool_versions={}, rows=rows())
    assert store.read_rows(run_id) == rows()


def test_runs_carry_kind_and_verdict_ok(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    cov = store.write_run(commit="a", tool_versions={}, rows=rows(), kind="coverage")
    ver = store.write_run(commit="a", tool_versions={}, rows=rows(), kind="verify")
    store.set_verdict_ok(ver, False)
    runs = {r["id"]: r for r in store.list_runs()}
    assert runs[cov]["kind"] == "coverage" and runs[cov]["verdict_ok"] is None
    assert runs[ver]["kind"] == "verify" and runs[ver]["verdict_ok"] is False


def test_default_baseline_skips_failed_verifies_and_hook_runs(tmp_path):
    from crapkit.store import default_baseline
    store = SnapshotStore(tmp_path / "crap.sqlite")
    cov = store.write_run(commit="a", tool_versions={}, rows=rows(), kind="coverage", lanes={"unit": {}})
    bad = store.write_run(commit="a", tool_versions={}, rows=rows(), kind="verify", lanes={"unit": {}})
    store.set_verdict_ok(bad, False)
    store.write_run(commit="a", tool_versions={}, rows=[], kind="hook", lanes={"h": {}})
    assert default_baseline(store)["id"] == cov, "failed verify and hook runs never become the baseline"
    good = store.write_run(commit="b", tool_versions={}, rows=rows(), kind="verify", lanes={"unit": {}})
    store.set_verdict_ok(good, True)
    assert default_baseline(store)["id"] == good, "a PASSING verify advances the baseline"


def test_migration_labels_pre_kind_rows_legacy_not_coverage(tmp_path):
    # A database written before the kind column existed must not have its rows
    # promoted to 'coverage' by the ALTER's DEFAULT: their provenance is unknown.
    import sqlite3
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
    conn.execute("INSERT INTO runs (commit_sha, tool_versions) VALUES ('old000', '{}')")
    conn.commit()
    conn.close()

    store = SnapshotStore(db)
    (old_run,) = store.list_runs()
    assert old_run["kind"] == "legacy"
    fresh = store.write_run(commit="new111", tool_versions={}, rows=rows())
    assert store.list_runs()[-1]["kind"] == "coverage"
    assert fresh != old_run["id"]


def test_partial_runs_are_never_trusted_baselines(tmp_path):
    from crapkit.store import trusted_runs
    store = SnapshotStore(tmp_path / "crap.sqlite")
    full = store.write_run(commit="c1", tool_versions={}, rows=rows(),
                           lanes={"unit": {}, "py": {}}, kind="coverage")
    store.write_run(commit="c2", tool_versions={}, rows=rows(),
                    lanes={"unit": {}}, kind="partial")
    assert [r["id"] for r in trusted_runs(store)] == [full], \
        "a --lane or failed-lane run must never serve as the verify baseline"


def test_read_overrides_all_returns_the_audit_trail(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="c1", tool_versions={}, rows=[], kind="hook")
    store.write_overrides(run_id, [("src/a.ts", "f( )", 90.0, "prod down")])
    trail = store.read_overrides_all()
    assert len(trail) == 1
    assert trail[0][:5] == (run_id, "src/a.ts", "f( )", 90.0, "prod down")


def scored_rows(n: int, path: str = "src/a.ts") -> list:
    from crapkit.score import ScoredRow
    scopes = ("src", "ui")
    return [ScoredRow(scopes[i % 2], f"{path}{i % 3}", f"f{i}( )", 1 + i, 9 + i,
                      3 + i % 9, 3 + i % 9, 3 + i % 9, 5, 1, 1,
                      0.25, "measured", 3.7 + i * 1.37, "add-tests")
            for i in range(n)]


def index_names(db) -> list[str]:
    import sqlite3
    conn = sqlite3.connect(db)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'functions'")]
    conn.close()
    return sorted(names)


def test_the_identity_index_lands_on_a_database_that_predates_it(tmp_path):
    # The index is a migration, not a fresh-database detail: an existing store
    # must grow it on the next open, and opening again must not duplicate it.
    import sqlite3
    db = tmp_path / "crap.sqlite"
    run_id = SnapshotStore(db).write_run(commit="abc", tool_versions={}, rows=rows())

    conn = sqlite3.connect(db)  # a store written before the index existed
    conn.execute("DROP INDEX idx_functions_identity")
    conn.commit()
    conn.close()
    assert "idx_functions_identity" not in index_names(db)

    migrated = SnapshotStore(db)
    assert "idx_functions_identity" in index_names(db), "opening the store migrates it"
    assert migrated.read_rows(run_id) == rows(), "rows read back unchanged across the migration"

    SnapshotStore(db)
    assert index_names(db).count("idx_functions_identity") == 1, "the migration is idempotent"


def test_run_totals_matches_digest_totals_run_for_run(tmp_path):
    # trend adds three numbers per run in SQL now; the same run must not read
    # one way through the aggregate and another way through the rows.
    from crapkit.digest import totals, totals_from_counts
    store = SnapshotStore(tmp_path / "crap.sqlite")
    for i in range(4):
        store.write_run(commit=f"c{i}", tool_versions={}, rows=scored_rows(200 + i * 37),
                        lanes={"unit": {}}, kind="coverage")
    targets = {"ui": 4}

    agg = store.run_totals(target=6, scope_targets=targets)

    for run in store.list_runs():
        from_rows = totals(store.read_scored(run["id"]), target=6, scope_targets=targets)
        assert totals_from_counts(*agg[run["id"]]) == from_rows, run["id"]


def test_run_totals_leaves_out_runs_that_scored_nothing(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    inventory_only = store.write_run(commit="c1", tool_versions={}, rows=rows())
    hook = store.write_run(commit="c2", tool_versions={}, rows=[], kind="hook")
    assert store.run_totals(target=6) == {}
    assert store.read_scored(inventory_only) == [] and store.read_rows(hook) == []


def test_min_ccn_returns_exactly_what_the_caller_would_have_kept(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="c1", tool_versions={}, rows=scored_rows(120),
                             lanes={"unit": {}}, kind="coverage")

    for floor in (0, 1, 5, 9, 99):
        assert store.read_scored(run_id, min_ccn=floor) == \
            [r for r in store.read_scored(run_id) if r.ccn >= floor], floor
        assert store.read_rows(run_id, min_ccn=floor) == \
            [r for r in store.read_rows(run_id) if r.ccn >= floor], floor
        assert store.count_scored_below(run_id, floor) == \
            len([r for r in store.read_scored(run_id) if r.ccn < floor]), floor


def test_function_span_matches_the_row_scan_it_replaced(tmp_path):
    from crapkit.snapshot import InventoryRow
    store = SnapshotStore(tmp_path / "crap.sqlite")
    # two functions sharing a name in one file: the span lookup must pick the
    # same one the export-ordered row scan used to pick
    twins = [InventoryRow("src", "src/a.ts", "f( )", 40, 55, 7, 7, 7, 5, 1, 1),
             InventoryRow("src", "src/a.ts", "f( )", 10, 22, 4, 4, 4, 5, 1, 1),
             InventoryRow("ui", "src/a.ts", "f( )", 70, 80, 4, 4, 4, 5, 1, 1)]
    run_id = store.write_run(commit="c1", tool_versions={}, rows=twins)

    first = next(r for r in store.read_rows(run_id) if (r.path, r.long_name) == ("src/a.ts", "f( )"))

    assert store.function_span(run_id, "src/a.ts", "f( )") == (first.start, first.end)
    assert store.function_span(run_id, "src/a.ts", "gone( )") is None


def test_function_history_reads_a_trajectory(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    from crapkit.score import ScoredRow
    for commit, cov in (("c1", 0.0), ("c2", 0.5)):
        row = ScoredRow("src", "src/a.ts", "f( )", 1, 9, 7, 7, 7, 5, 1, 1, cov, "measured",
                        7 * 7 * (1 - cov) ** 3 + 7, "add-tests")
        store.write_run(commit=commit, tool_versions={}, rows=[row], lanes={"py": {}}, kind="coverage")
    hist = store.function_history("src/a.ts", "f( )")
    assert [h["commit"] for h in hist] == ["c1", "c2"]
    assert hist[1]["cov"] == 0.5 and hist[0]["ccn"] == 7


# --- the verdict the worklist reads off a scored run --------------------------

def debt_rows():
    """One row per remedy, plus a ccn-2 row under any pushdown floor."""
    from crapkit.score import ScoredRow

    def row(path, name, ccn, crap, remedy, scope="src", flag="measured"):
        return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 8, 1, 2,
                         0.0, flag, crap, remedy)

    return [row("src/a.ts", "big( x )", 9, 90.0, "decompose"),
            row("src/b.ts", "dark( x )", 4, 20.0, "add-tests", flag="untested"),
            row("src/c.ts", "fine( x )", 5, 5.0, "ok"),
            row("util/d.py", "tiny( x )", 2, 6.0, "add-tests", scope="util")]


def debt(marks) -> set:
    return {key for key, (_, remedy) in marks.verdicts.items() if remedy != "ok"}


def test_read_marks_carries_the_flag_and_the_remedy_of_every_scored_row(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc", tool_versions={}, rows=debt_rows())

    marks = store.read_marks(run_id)

    assert marks.verdicts[("src/b.ts", "dark( x )")] == ("untested", "add-tests")
    assert marks.verdicts[("src/c.ts", "fine( x )")] == ("measured", "ok")
    assert debt(marks) == {("src/a.ts", "big( x )"), ("src/b.ts", "dark( x )"),
                           ("util/d.py", "tiny( x )")}


def test_read_marks_decides_debt_the_way_the_queue_does(tmp_path):
    """The rule lives twice: `remedy != 'ok'` off these marks, and `r.remedy !=
    "ok"` in the ranking. They have to answer the same, or the worklist floor
    starts hiding rows next-item hands out."""
    from crapkit.cli import _actionable

    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc", tool_versions={}, rows=debt_rows())

    assert debt(store.read_marks(run_id)) == {
        (r.path, r.long_name) for r in _actionable(store.read_scored(run_id))}


def test_read_marks_takes_the_same_floor_and_scope_cut(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc", tool_versions={}, rows=debt_rows())

    assert debt(store.read_marks(run_id, min_ccn=3)) == {("src/a.ts", "big( x )"),
                                                         ("src/b.ts", "dark( x )")}
    assert debt(store.read_marks(run_id, scopes=["util"])) == {("util/d.py", "tiny( x )")}


def test_read_marks_reports_the_worst_of_two_twins(tmp_path):
    """Twins share (path, long_name). The worst one represents the key, so a
    finished sibling can never mark the pair done."""
    from crapkit.score import ScoredRow

    def twin(start, crap, remedy):
        return ScoredRow("src", "src/t.ts", "twin( x )", start, start + 8, 7, 7, 7, 8, 1, 2,
                         0.0, "measured", crap, remedy)

    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc", tool_versions={},
                             rows=[twin(1, 56.0, "decompose"), twin(20, 5.0, "ok")])

    assert store.read_marks(run_id).verdicts[("src/t.ts", "twin( x )")] == ("measured", "decompose")


def test_an_inventory_run_has_no_verdict_to_report(tmp_path):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc", tool_versions={}, rows=rows())

    marks = store.read_marks(run_id)

    assert (marks.verdicts, marks.scores) == ({}, {}), "no remedy column, no verdict"


def test_read_marks_carries_the_score_and_the_coverage_beside_the_verdict(tmp_path):
    """The ranking view of a CRAP scorer prints the CRAP score. Reading it in
    the same query as the verdict costs two columns, never a second read."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc", tool_versions={}, rows=debt_rows())

    marks = store.read_marks(run_id)

    assert marks.verdicts[("src/b.ts", "dark( x )")] == ("untested", "add-tests")
    assert marks.scores[("src/b.ts", "dark( x )", 1)] == (20.0, 0.0)
    assert marks.scores[("src/c.ts", "fine( x )", 1)] == (5.0, 0.0)


def test_read_marks_keeps_each_twins_own_score(tmp_path):
    """The verdict collapses to the worst twin so admission can protect the
    pair; the score does not, or the finished twin's row prints its sibling's
    CRAP as its own."""
    from crapkit.score import ScoredRow

    def twin(start, crap, remedy):
        return ScoredRow("src", "src/t.ts", "twin( x )", start, start + 8, 7, 7, 7, 8, 1, 2,
                         0.0, "measured", crap, remedy)

    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc", tool_versions={},
                             rows=[twin(1, 56.0, "decompose"), twin(20, 5.0, "ok")])

    scores = store.read_marks(run_id).scores

    assert scores[("src/t.ts", "twin( x )", 1)] == (56.0, 0.0)
    assert scores[("src/t.ts", "twin( x )", 20)] == (5.0, 0.0)


# --- the twin keys a cut read cannot count ------------------------------------

def test_twin_key_names_covers_only_the_functions_that_share_a_name_in_their_file(tmp_path):
    """The worklist reads rows cut at a floor and a scope. Counting twin
    ordinals over that cut hands twin #2 the bare key, and with it twin #1's
    mark, so the count runs over the whole run here and names only the rows
    whose key carries an ordinal."""
    from crapkit.score import ScoredRow

    def fn(path, name, start):
        return ScoredRow("src", path, name, start, start + 8, 7, 7, 7, 8, 1, 2,
                         0.0, "measured", 56.0, "decompose")

    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="abc", tool_versions={},
                             rows=[fn("src/t.ts", "twin( x )", 20), fn("src/t.ts", "twin( x )", 1),
                                   fn("src/t.ts", "lone( x )", 40), fn("src/u.ts", "twin( x )", 1)])

    assert store.twin_key_names(run_id) == {("src/t.ts", "twin( x )", 1): "twin( x )",
                                            ("src/t.ts", "twin( x )", 20): "twin( x )#2"}


def test_twin_key_names_reads_one_run_and_not_its_neighbours(tmp_path):
    from crapkit.score import ScoredRow

    def fn(start):
        return ScoredRow("src", "src/t.ts", "twin( x )", start, start + 8, 7, 7, 7, 8, 1, 2,
                         0.0, "measured", 56.0, "decompose")

    store = SnapshotStore(tmp_path / "crap.sqlite")
    twinned = store.write_run(commit="c1", tool_versions={}, rows=[fn(1), fn(20)])
    alone = store.write_run(commit="c2", tool_versions={}, rows=[fn(1)])

    assert len(store.twin_key_names(twinned)) == 2
    assert store.twin_key_names(alone) == {}
