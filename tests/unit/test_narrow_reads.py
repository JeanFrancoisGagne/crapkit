"""Reads that pull the columns their caller actually uses.

digest builds two whole runs of scored rows and reads six of the
fields; doctor builds one and reads three, then groups a hundred thousand rows
in Python to answer a question about a few thousand directories. Both are the
same waste, and both are fixed the same way: the projection and the grouping go
into the query, and the answer above has to come out unchanged.
"""
from path_counts import path_counts

from crapkit.config import Config
from crapkit.digest import build_digest
from crapkit.doctor import UnmeasuredDir, unmeasured_directories
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

SCOPES = ("api", "ui")
FLAGS = ("measured", "untested", "no-lane", "cc-only")
REMEDIES = ("ok", "add-tests", "decompose", "split-lines")
FLAT = Config(target=6).ceiling_of  # every scope judged at the repo ceiling


def scored(n: int, bump: float = 0.0) -> list:
    return [ScoredRow(SCOPES[i % 2], f"src/m{i % 3}.py", f"f{i % 5}( a )",
                      1 + i, 9 + i, 3 + i % 7, 3 + i % 7, 3 + i % 7, 5, 1, 1,
                      0.25, FLAGS[i % 4], 4.0 + i + bump, REMEDIES[i % 3], i % 3)
            for i in range(n)]


def traced(store, call) -> list[str]:
    """Every statement the store ran during `call`."""
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        call()
    finally:
        store._conn.set_trace_callback(None)
    return seen


def seeded(tmp_path, *runs) -> tuple[SnapshotStore, list[int]]:
    store = SnapshotStore(tmp_path / "crap.sqlite")
    return store, [store.write_run(commit=f"c{i}", tool_versions={}, rows=rows,
                                   lanes={"unit": {}})
                   for i, rows in enumerate(runs)]


# --- digest keeps only the fields its identity and score comparison use ------

def test_the_digest_read_matches_the_wide_read_field_for_field(tmp_path):
    rows = scored(60)
    store, (run_id,) = seeded(tmp_path, rows)

    narrow = store.read_crap(run_id)
    wide = store.read_scored(run_id)

    assert [(r.scope, r.path, r.long_name, r.crap, r.start, r.occurrence) for r in wide] == \
        [tuple(r) for r in narrow], "same rows, same order, same values"


def test_the_digest_read_keeps_the_start_needed_to_distinguish_twins(tmp_path):
    """The mechanism, not the timing: a read that still names sixteen columns
    has not saved anything however fast the machine is."""
    store, (run_id,) = seeded(tmp_path, scored(20))

    (select,) = [s for s in traced(store, lambda: store.read_crap(run_id))
                 if s.lstrip().upper().startswith("SELECT")]

    projection = select.split(" FROM ")[0].strip()
    assert projection == "SELECT i.scope, i.path, i.long_name, f.crap, f.start, f.occurrence", \
        f"the digest read still pulls columns nothing reads: {projection}"


def test_the_digest_lines_are_identical_from_the_narrow_rows(tmp_path):
    """The contract build_digest has to keep: it names six fields and nothing
    else, so the narrow rows drive it to the same lines the wide rows did."""
    prev, cur = scored(60), scored(60, bump=3.0)
    store, (a, b) = seeded(tmp_path, prev, cur)

    wide = build_digest(store.read_scored(a), store.read_scored(b), ceiling_of=FLAT)
    narrow = build_digest(store.read_crap(a), store.read_crap(b), ceiling_of=FLAT)

    assert narrow.lines, "the fixture has to move something or this proves nothing"
    assert narrow == wide


def test_an_unchanged_pair_still_digests_to_silence(tmp_path):
    rows = scored(40)
    store, (a, b) = seeded(tmp_path, rows, rows)

    assert build_digest(store.read_crap(a), store.read_crap(b), ceiling_of=FLAT).quiet


def test_the_digest_read_skips_the_rows_no_run_scored(tmp_path):
    from crapkit.snapshot import InventoryRow

    rows = scored(20)
    store, (run_id,) = seeded(tmp_path, [InventoryRow(*r[:11], r.cognitive) for r in rows])

    assert store.read_crap(run_id) == [], "an inventory row has no crap to compare"


# --- doctor groups the run in SQL, not in Python -----------------------------

def flagged(path: str, flag: str, scope: str = "src", n: int = 1) -> list:
    return [ScoredRow(scope, path, f"f{i}( )", 1 + i, 9 + i, 3, 3, 3, 5, 1, 1,
                      1.0 if flag == "measured" else 0.0, flag, 3.0, "ok", 0)
            for i in range(n)]


GAPPY = (flagged("src/measured.py", "measured")
         + flagged("src/quiet/mod.py", "untested", n=2)
         + flagged("src/quiet/other.py", "untested")
         + flagged("shims/mod.py", "untested", scope="shims"))

TRACKED = ["src/measured.py", "src/quiet/mod.py", "src/quiet/other.py",
           "shims/mod.py", "tests/test_mod.py", "tests/test_measured.py"]


def test_the_path_counts_match_grouping_the_rows_by_hand(tmp_path):
    """The rule tests reach doctor.unmeasured_directories through path_counts,
    so the store's grouping and that one have to agree row for row."""
    store, (run_id,) = seeded(tmp_path, GAPPY)

    counts = store.count_by_path(run_id, flag="untested")

    assert counts == path_counts(store.read_scored(run_id))


def test_the_skipped_scopes_never_reach_the_counts(tmp_path):
    """coverage_optional is decided in the WHERE now. A scope filtered after the
    fact is a scope whose rows were read for nothing."""
    store, (run_id,) = seeded(tmp_path, GAPPY)
    skip = frozenset({"shims"})

    counts = store.count_by_path(run_id, flag="untested", skip_scopes=skip)

    assert counts == path_counts(store.read_scored(run_id), skip=skip)
    assert "shims/mod.py" not in [path for path, _n, _o in counts]


def test_the_doctor_read_asks_for_three_columns_and_groups_them(tmp_path):
    store, (run_id,) = seeded(tmp_path, GAPPY)

    (select,) = [s for s in traced(store, lambda: store.count_by_path(run_id, flag="untested"))
                 if s.lstrip().upper().startswith("SELECT")]

    projection = select.split(" FROM ")[0].strip()  # the trace expands the bound code
    assert projection.startswith("SELECT i.path, COUNT(*), SUM(f.flag IS NOT "), projection
    assert "GROUP BY i.path" in select, f"the run is still grouped in Python: {select}"


def test_the_store_counts_name_the_unmeasured_directory(tmp_path):
    """The path doctor runs: the SQL grouping, straight into the one rule. src/quiet
    holds three untested functions and tests/test_mod.py names mod.py there; the
    shims scope is coverage_optional and stays out."""
    store, (run_id,) = seeded(tmp_path, GAPPY)

    counts = store.count_by_path(run_id, flag="untested", skip_scopes=frozenset({"shims"}))

    assert unmeasured_directories(counts, TRACKED) == (
        UnmeasuredDir("src/quiet", 3, "tests/test_mod.py"),)


def test_a_measured_function_anywhere_clears_its_directory(tmp_path):
    rows = flagged("src/quiet/mod.py", "measured") + flagged("src/quiet/other.py", "untested")
    store, (run_id,) = seeded(tmp_path, rows)

    assert unmeasured_directories(store.count_by_path(run_id, flag="untested"), TRACKED) == ()


def test_a_scope_now_marked_coverage_optional_is_skipped_even_in_older_rows(tmp_path):
    """The store still holds the run that scored before coverage_optional was
    set; those rows say untested, and the check must not re-open the question."""
    tracked = ["shims/mod.py", "tests/test_mod.py"]
    store, (run_id,) = seeded(tmp_path, flagged("shims/mod.py", "untested", scope="shims"))

    skipped = store.count_by_path(run_id, flag="untested", skip_scopes=frozenset({"shims"}))
    kept = store.count_by_path(run_id, flag="untested")

    assert unmeasured_directories(skipped, tracked) == ()
    assert [g.directory for g in unmeasured_directories(kept, tracked)] == ["shims"]
