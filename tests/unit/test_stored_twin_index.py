"""A run's shingle index is stored once and read back by every later process.

`brief` used to shingle every function in the repo on every call to find one
function's twins: 77,816 functions and 1.87 M shingles on a large consumer repo,
about 2.4 s of a 4.4 s brief. The shingles were builtin `hash()` values, which
CPython salts per process, so nothing could be kept between calls.

A shingle is now an 8-byte blake2b digest, the same in every process, and the
store keeps one run's inverted index: digest to the functions that hold it, and
each function's shingle count. What this pins is that the stored index answers
exactly what a fresh build answers, that a second process reads it instead of
building, and that it lives and dies with its run.
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from crapkit.dup import find_duplicates, find_twins, function_index, twins_in
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore, prune_keep_set


def row(path, name, start, end, scope="src"):
    return InventoryRow(scope=scope, path=path, long_name=name, start=start, end=end,
                        ccn_std=3, ccn_mod=3, ccn=3, nloc=end - start + 1, params=1, nesting=1)


BODY = "\n".join(f"    step_{i} = compute({i}) + offset" for i in range(10))
GROWN = BODY + "\n" + "\n".join(f"    extra_{i} = more({i})" for i in range(4))
DEEPER = "\n".join(f"        step_{i} = compute({i}) + offset" for i in range(10))
OTHER = "\n".join(f"    other_{i} = load({i})" for i in range(10))

SOURCES = {
    "src/a.py": "def alpha():\n" + BODY + "\n",
    "src/b.py": "def beta():\n" + GROWN + "\n",
    "src/c.py": "def gamma():\n" + OTHER + "\n",
    # outer 1..22 holds inner 12..22: a contained twin, never a duplicate pair
    "src/d.py": "def outer():\n" + BODY + "\n    def inner():\n" + DEEPER + "\n",
}
ROWS = [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 15),
        row("src/c.py", "gamma", 1, 11), row("src/d.py", "outer", 1, 22),
        row("src/d.py", "inner", 12, 22), row("src/a.py", "alpha", 1, 11, scope="lib")]


def refuse_build():
    raise AssertionError("the stored index was built again")


def refuse_sources():
    raise AssertionError("a stored index read the repo's files")


def build():
    return function_index(ROWS, SOURCES)


def seeded(tmp_path, runs=1):
    store = SnapshotStore(tmp_path / "crap.sqlite")
    ids = [store.write_run(commit=f"c{i}", tool_versions={}, rows=ROWS, kind="inventory")
           for i in range(runs)]
    return store, ids


def index_rows(db) -> dict:
    conn = sqlite3.connect(db)
    try:
        return {table: conn.execute(f"SELECT DISTINCT run_id FROM {table} ORDER BY run_id").fetchall()
                for table in ("twin_runs", "twin_functions", "twin_postings")}
    finally:
        conn.close()


def held_by(db, run_id) -> bool:
    return all((run_id,) in ids for ids in index_rows(db).values())


# --- the stored index answers what a fresh build answers ----------------------

def test_a_stored_index_finds_the_twins_a_fresh_build_finds(tmp_path):
    store, (run_id,) = seeded(tmp_path)
    store.twin_index(run_id, build)

    stored = SnapshotStore(tmp_path / "crap.sqlite").twin_index(run_id, refuse_build)

    for target in ROWS:
        assert twins_in(stored, target, SOURCES[target.path]) == find_twins(target, ROWS, SOURCES)
    assert [t["long_name"] for t in twins_in(stored, ROWS[0], SOURCES["src/a.py"])] == \
        ["beta", "outer", "inner"]


def test_the_first_ask_builds_and_hands_back_the_same_twins(tmp_path):
    store, (run_id,) = seeded(tmp_path)

    built = store.twin_index(run_id, build)

    assert twins_in(built, ROWS[3], SOURCES["src/d.py"]) == find_twins(ROWS[3], ROWS, SOURCES)
    assert held_by(tmp_path / "crap.sqlite", run_id)


def test_a_target_edited_since_the_run_is_shingled_from_its_text_now(tmp_path):
    """brief shingles the target from the file as it is, and only the other
    side comes from the run: the same split a per-call build made when the
    target's file changed and the rest of the repo did not."""
    store, (run_id,) = seeded(tmp_path)
    store.twin_index(run_id, build)
    stored = SnapshotStore(tmp_path / "crap.sqlite").twin_index(run_id, refuse_build)
    edited = "def gamma():\n" + BODY + "\n"

    assert twins_in(stored, ROWS[2], edited) == \
        find_twins(ROWS[2], ROWS, {**SOURCES, "src/c.py": edited}, indexed=build())


def test_a_target_rewritten_past_every_stored_shingle_has_no_twins(tmp_path):
    store, (run_id,) = seeded(tmp_path)
    store.twin_index(run_id, build)
    stored = SnapshotStore(tmp_path / "crap.sqlite").twin_index(run_id, refuse_build)
    rewritten = "def alpha():\n" + "\n".join(f"    fresh_{i} = new({i})" for i in range(10)) + "\n"

    assert twins_in(stored, ROWS[0], rewritten) == []


def test_a_target_whose_file_is_gone_has_no_twins(tmp_path):
    store, (run_id,) = seeded(tmp_path)

    assert twins_in(store.twin_index(run_id, build), ROWS[0], None) == []


def test_duplication_reads_its_pairs_from_the_stored_index_and_no_file(tmp_path):
    store, (run_id,) = seeded(tmp_path)
    store.twin_index(run_id, build)
    stored = SnapshotStore(tmp_path / "crap.sqlite").twin_index(run_id, refuse_build)

    for top in (1, 50, -1):
        assert find_duplicates(ROWS, refuse_sources, top=top, indexed=stored) == \
            find_duplicates(ROWS, lambda: SOURCES, top=top)
    # both alpha scope copies pair with beta, outer and inner at 7/8, and beta
    # pairs with inner at 7/8; beta and outer share 7 of 12 and stay out, and
    # outer holds inner and the two alphas share a span, so neither is a pair
    assert len(find_duplicates(ROWS, refuse_sources, indexed=stored)) == 7


def test_duplication_at_another_min_lines_builds_its_own(tmp_path):
    store, (run_id,) = seeded(tmp_path)
    stored = store.twin_index(run_id, build)

    assert find_duplicates(ROWS, lambda: SOURCES, min_lines=4, indexed=stored) == \
        find_duplicates(ROWS, lambda: SOURCES, min_lines=4)


# --- stable across processes --------------------------------------------------

def test_another_process_reads_the_same_twins_back(tmp_path):
    """The failure the stable digest exists for: one index written and read
    back under builtin hash() shared 0 of 37 shingles in a fresh interpreter,
    and every twin vanished on a repo that had not changed."""
    store, (run_id,) = seeded(tmp_path)
    store.twin_index(run_id, build)
    source = tmp_path / "a.py"
    source.write_text(SOURCES["src/a.py"], encoding="utf-8")
    code = ("import json, sys\n"
            "from crapkit.dup import twins_in\n"
            "from crapkit.snapshot import InventoryRow\n"
            "from crapkit.store import SnapshotStore\n"
            "refuse = lambda: sys.exit('rebuilt')\n"
            "index = SnapshotStore(sys.argv[1]).twin_index(int(sys.argv[2]), refuse)\n"
            "target = InventoryRow('src', 'src/a.py', 'alpha', 1, 11, 3, 3, 3, 11, 1, 1)\n"
            "text = open(sys.argv[3], encoding='utf-8').read()\n"
            "print(json.dumps(twins_in(index, target, text)))\n")
    expected = find_twins(ROWS[0], ROWS, SOURCES)
    assert expected, "the fixture has twins to lose"
    for seed in ("1", "2", "3"):
        process = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path / "crap.sqlite"), str(run_id), str(source)],
            env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, check=True)
        assert json.loads(process.stdout) == expected, f"seed {seed}"


# --- one index, for the run it describes --------------------------------------

def test_a_newer_runs_index_replaces_the_older_one(tmp_path):
    store, (first, second) = seeded(tmp_path, runs=2)
    store.twin_index(first, build)

    store.twin_index(second, build)

    assert index_rows(tmp_path / "crap.sqlite") == {
        "twin_runs": [(second,)], "twin_functions": [(second,)], "twin_postings": [(second,)]}


def test_an_older_runs_index_leaves_the_newer_one_standing(tmp_path):
    """brief reads the newest trusted run and duplication the newest run with
    rows. When those differ, each keeps its own index rather than evicting the
    other's on every call."""
    store, (first, second) = seeded(tmp_path, runs=2)
    store.twin_index(second, build)

    store.twin_index(first, build)

    assert held_by(tmp_path / "crap.sqlite", first) and held_by(tmp_path / "crap.sqlite", second)


def test_prune_takes_a_runs_index_with_it(tmp_path):
    store, (first, second) = seeded(tmp_path, runs=2)
    store.twin_index(second, build)
    store.twin_index(first, build)

    store.prune_runs({second}, observed_ids={first, second})

    assert index_rows(tmp_path / "crap.sqlite") == {
        "twin_runs": [(second,)], "twin_functions": [(second,)], "twin_postings": [(second,)]}
    assert prune_keep_set(store.list_runs(), set(), keep=1) == {second}


def test_an_index_for_a_run_pruned_meanwhile_is_never_written(tmp_path):
    store, (first, second) = seeded(tmp_path, runs=2)

    def pruned_while_building():
        SnapshotStore(tmp_path / "crap.sqlite").prune_runs({second}, observed_ids={first})
        return build()

    answer = store.twin_index(first, pruned_while_building)

    assert twins_in(answer, ROWS[0], SOURCES["src/a.py"]) == find_twins(ROWS[0], ROWS, SOURCES)
    assert index_rows(tmp_path / "crap.sqlite") == {
        "twin_runs": [], "twin_functions": [], "twin_postings": []}


def test_an_index_another_process_stored_first_is_not_written_twice(tmp_path):
    store, (run_id,) = seeded(tmp_path)

    def raced():
        SnapshotStore(tmp_path / "crap.sqlite").twin_index(run_id, build)
        return build()

    answer = store.twin_index(run_id, raced)

    assert twins_in(answer, ROWS[0], SOURCES["src/a.py"]) == find_twins(ROWS[0], ROWS, SOURCES)
    stored = SnapshotStore(tmp_path / "crap.sqlite").twin_index(run_id, refuse_build)
    assert twins_in(stored, ROWS[0], SOURCES["src/a.py"]) == find_twins(ROWS[0], ROWS, SOURCES)


def test_a_store_that_cannot_take_the_write_still_answers(tmp_path):
    """Best effort, like the rollup: another process holding the write lock
    costs the next caller the speedup, never this command its answer."""
    store, (run_id,) = seeded(tmp_path)
    holder = sqlite3.connect(tmp_path / "crap.sqlite")
    holder.execute("BEGIN IMMEDIATE")  # a writer mid-transaction: reads pass, writes wait
    store._conn.execute("PRAGMA busy_timeout = 0")
    try:
        answer = store.twin_index(run_id, build)
    finally:
        holder.rollback()
        holder.close()

    assert twins_in(answer, ROWS[0], SOURCES["src/a.py"]) == find_twins(ROWS[0], ROWS, SOURCES)
    assert not held_by(tmp_path / "crap.sqlite", run_id)


def test_an_index_from_another_shingle_format_is_built_again(tmp_path):
    """A stored digest is only comparable with a target shingled the same way.
    An index written under another format would match nothing and report no
    twins, so it reads as absent and is replaced."""
    store, (run_id,) = seeded(tmp_path)
    store.twin_index(run_id, build)
    conn = sqlite3.connect(tmp_path / "crap.sqlite")
    conn.execute("UPDATE twin_runs SET shingle_format = 'hash() of a tuple'")
    conn.commit()
    conn.close()
    calls = []

    def counted():
        calls.append(1)
        return build()

    SnapshotStore(tmp_path / "crap.sqlite").twin_index(run_id, counted)
    SnapshotStore(tmp_path / "crap.sqlite").twin_index(run_id, counted)

    assert calls == [1], "rebuilt once, then read back"


def test_a_store_from_before_the_index_grows_its_tables_on_open(tmp_path):
    store, (run_id,) = seeded(tmp_path)
    store._conn.executescript("DROP TABLE twin_runs; DROP TABLE twin_functions; "
                              "DROP TABLE twin_postings;")
    store._conn.close()

    reopened = SnapshotStore(tmp_path / "crap.sqlite")
    reopened.twin_index(run_id, build)

    assert held_by(tmp_path / "crap.sqlite", run_id)


@pytest.mark.parametrize("body_lines", [8, 1300])
def test_a_lookup_of_any_size_answers_every_digest(tmp_path, body_lines):
    """SQLite caps the parameters one statement takes, so a target with more
    shingles than the cap is asked in pieces and every piece is counted."""
    lines = [f"    v{i} = step({i})" for i in range(body_lines)]
    text = "def big():\n" + "\n".join(lines) + "\n"
    rows = [row("src/x.py", "big", 1, len(lines) + 1), row("src/y.py", "big2", 1, len(lines) + 1)]
    sources = {"src/x.py": text, "src/y.py": text.replace("big", "big2", 1)}
    store = SnapshotStore(tmp_path / "crap.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=rows, kind="inventory")
    store.twin_index(run_id, lambda: function_index(rows, sources))
    stored = SnapshotStore(tmp_path / "crap.sqlite").twin_index(run_id, refuse_build)

    expected = find_twins(rows[0], rows, sources)
    assert [t["path"] for t in expected] == ["src/y.py"]
    assert twins_in(stored, rows[0], text) == expected
