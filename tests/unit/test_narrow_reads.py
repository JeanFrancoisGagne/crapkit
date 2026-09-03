"""Reads that pull the columns their caller actually uses.

digest builds two whole runs of sixteen-field rows and reads four of the
fields; doctor builds one and reads three, then groups a hundred thousand rows
in Python to answer a question about a few thousand directories. Both are the
same waste, and both are fixed the same way: the projection and the grouping go
into the query, and the answer above has to come out unchanged.
"""
from crapkit.config import Config
from crapkit.digest import build_digest
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

SCOPES = ("api", "ui")
FLAGS = ("measured", "untested", "no-lane", "cc-only")
REMEDIES = ("ok", "add-tests", "decompose")
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


# --- digest reads four columns, not sixteen ----------------------------------

def test_the_digest_read_matches_the_wide_read_field_for_field(tmp_path):
    rows = scored(60)
    store, (run_id,) = seeded(tmp_path, rows)

    narrow = store.read_crap(run_id)
    wide = store.read_scored(run_id)

    assert [(r.scope, r.path, r.long_name, r.crap) for r in wide] == \
        [tuple(r) for r in narrow], "same rows, same order, same values"


def test_the_digest_read_asks_for_four_columns(tmp_path):
    """The mechanism, not the timing: a read that still names sixteen columns
    has not saved anything however fast the machine is."""
    store, (run_id,) = seeded(tmp_path, scored(20))

    (select,) = [s for s in traced(store, lambda: store.read_crap(run_id))
                 if s.lstrip().upper().startswith("SELECT")]

    projection = select.split(" FROM ")[0].strip()
    assert projection == "SELECT i.scope, i.path, i.long_name, f.crap", \
        f"the digest read still pulls columns nothing reads: {projection}"


def test_the_digest_lines_are_identical_from_the_narrow_rows(tmp_path):
    """The contract build_digest has to keep: it names four fields and nothing
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


def group_in_python(rows: list, flag: str, skip: frozenset) -> list[tuple]:
    """What count_by_path replaces: one pass over every scored row of the run."""
    counts: dict[str, list] = {}
    for r in rows:
        if r.scope in skip:
            continue
        entry = counts.setdefault(r.path, [0, 0])
        entry[0] += 1
        entry[1] += r.flag != flag
    return [(path, n, other) for path, (n, other) in sorted(counts.items())]


def test_the_path_counts_match_grouping_the_rows_by_hand(tmp_path):
    store, (run_id,) = seeded(tmp_path, GAPPY)

    counts = store.count_by_path(run_id, flag="untested")

    assert counts == group_in_python(store.read_scored(run_id), "untested", frozenset())


def test_the_skipped_scopes_never_reach_the_counts(tmp_path):
    """coverage_optional is decided in the WHERE now. A scope filtered after the
    fact is a scope whose rows were read for nothing."""
    store, (run_id,) = seeded(tmp_path, GAPPY)
    skip = frozenset({"shims"})

    counts = store.count_by_path(run_id, flag="untested", skip_scopes=skip)

    assert counts == group_in_python(store.read_scored(run_id), "untested", skip)
    assert "shims/mod.py" not in [path for path, _n, _o in counts]


def test_the_doctor_read_asks_for_three_columns_and_groups_them(tmp_path):
    store, (run_id,) = seeded(tmp_path, GAPPY)

    (select,) = [s for s in traced(store, lambda: store.count_by_path(run_id, flag="untested"))
                 if s.lstrip().upper().startswith("SELECT")]

    projection = select.split(" FROM ")[0].strip()  # the trace expands the bound code
    assert projection.startswith("SELECT i.path, COUNT(*), SUM(f.flag IS NOT "), projection
    assert "GROUP BY i.path" in select, f"the run is still grouped in Python: {select}"


def test_the_gaps_from_the_counts_are_the_gaps_from_the_rows(tmp_path):
    """The differential that matters: the SQL grouping and the shipped Python
    scan must name the same directories, with the same counts and the same
    example test, in the same order."""
    from crapkit.cli.admin import _unmeasured_gaps
    from crapkit.doctor import unmeasured_directories

    store, (run_id,) = seeded(tmp_path, GAPPY)
    skip = frozenset({"shims"})

    from_sql = _unmeasured_gaps(store.count_by_path(run_id, flag="untested", skip_scopes=skip),
                                TRACKED)
    from_rows = unmeasured_directories(store.read_scored(run_id), TRACKED, skip_scopes=skip)

    assert from_sql == from_rows
    assert [g.directory for g in from_sql] == ["src/quiet"], "the fixture has to find one gap"
    assert from_sql[0].functions == 3


def test_a_measured_function_anywhere_clears_its_directory(tmp_path):
    from crapkit.cli.admin import _unmeasured_gaps
    from crapkit.doctor import unmeasured_directories

    rows = flagged("src/quiet/mod.py", "measured") + flagged("src/quiet/other.py", "untested")
    store, (run_id,) = seeded(tmp_path, rows)

    counts = store.count_by_path(run_id, flag="untested")
    assert _unmeasured_gaps(counts, TRACKED) == ()
    assert unmeasured_directories(store.read_scored(run_id), TRACKED) == ()
