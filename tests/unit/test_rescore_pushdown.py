"""`rescore` reads the files it rescores, not the run they sit in.

The overlay needs the baseline run's coverage for a handful of paths. It got it
by reading every scored row of the run and dropping the ones whose path was not
in the set — 140,000 rows materialized into ScoredRows to keep fifty of them,
measured at 799.5 ms against 0.8 ms for the per-path read that already exists on
the store. `crapkit watch` spawns `rescore` on every saved file, so that read is
the bulk of its per-change cycle.

Two things have to hold at once: the mechanism (no statement reads the whole
run) and the answer (the overlay, its table and its JSON come out byte for
byte identical to what the wide read produced). The equivalence tests below
build the wide list by hand and compare, so a future change to either read
fails here rather than in a consumer's diff.
"""
import io
import json
import time
from contextlib import redirect_stdout

import pytest

from crapkit.cli import _baseline_rows, _print_rescore_table, _rescore_json, _rescore_overlay
from crapkit.config import Config, Scope
from crapkit.score import ScoredRow
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore

PATHS = ("src/a.py", "src/b.py", "src/c.py", "ui/d.py")
LATEST = {"id": 1, "commit": "0123456789abcdef", "lanes": {"unit": {"scopes": ["src", "ui"]}}}
CFG = Config(target=6, scopes=(Scope("src", ("src",), ("python",)),
                               Scope("ui", ("ui",), ("python",))))


def scope_of(path: str) -> str:
    return path.split("/")[0]


def scored_run(per_file: int = 6) -> list[ScoredRow]:
    """Twins included: two functions share a long_name in each file, which is
    what makes the row ORDER of the baseline list observable in the overlay."""
    return [ScoredRow(scope_of(path), path, f"f{i % 3}( a )", 1 + i * 9, 8 + i * 9,
                      3 + i, 3 + i, 3 + i, 12, 2, 1, 0.5, "measured",
                      4.0 + i, "ok", 0)
            for path in PATHS for i in range(per_file)]


def fresh_rows() -> list[InventoryRow]:
    """The working tree's complexity for the two rescored files."""
    return [InventoryRow(scope_of(path), path, f"f{i % 3}( a )", 1 + i * 9, 8 + i * 9,
                         3 + i, 3 + i, 3 + i, 12, 2, 1, 0)
            for path in ("src/a.py", "src/c.py") for i in range(6)]


@pytest.fixture()
def store(tmp_path) -> SnapshotStore:
    st = SnapshotStore(tmp_path / "crap.sqlite")
    st.write_run(commit=LATEST["commit"], tool_versions={}, rows=scored_run(),
                 lanes=LATEST["lanes"])
    return st


def wide_read(store: SnapshotStore, flat: list[str]) -> list[ScoredRow]:
    """What the overlay used to be handed: the whole run, filtered in Python."""
    in_scope = set(flat)
    return [r for r in store.read_scored(LATEST["id"]) if r.path in in_scope]


def traced(store: SnapshotStore, call) -> list[str]:
    seen: list[str] = []
    store._conn.set_trace_callback(seen.append)
    try:
        call()
    finally:
        store._conn.set_trace_callback(None)
    return seen


# --- the rows are the same rows, in the same order ----------------------------

def test_the_narrow_read_returns_the_wide_read_row_for_row(store: SnapshotStore):
    flat = ["src/a.py", "src/c.py"]

    assert _baseline_rows(store, LATEST["id"], flat) == wide_read(store, flat)


def test_the_order_holds_when_the_files_span_two_scopes(store: SnapshotStore):
    """Row order is `scope, path, start, end, long_name`, so reading path by
    path and concatenating is NOT the store's order — the read re-sorts, or the
    nearest-start pick among twins can land on a different baseline row."""
    flat = ["src/b.py", "ui/d.py"]

    rows = _baseline_rows(store, LATEST["id"], flat)

    assert rows == wide_read(store, flat)
    assert [r.scope for r in rows] == ["src"] * 6 + ["ui"] * 6


def test_a_path_the_run_never_scored_contributes_nothing(store: SnapshotStore):
    assert _baseline_rows(store, LATEST["id"], ["src/gone.py"]) == []


def test_an_empty_file_list_reads_nothing_at_all(store: SnapshotStore):
    assert _baseline_rows(store, LATEST["id"], []) == []


# --- the mechanism: no statement reads the run --------------------------------

def test_no_statement_selects_the_whole_run(store: SnapshotStore):
    """The point of the ticket. A read that still names the run without a path
    predicate has saved nothing however fast the machine is."""
    flat = ["src/a.py", "src/c.py"]

    selects = [s for s in traced(store, lambda: _baseline_rows(store, LATEST["id"], flat))
               if s.lstrip().upper().startswith("SELECT")]

    assert len(selects) == 2, selects
    assert all("i.path = " in s for s in selects), selects


def test_the_overlay_takes_the_same_narrow_route(store: SnapshotStore):
    """Through the real call site, not just the helper it was extracted into."""
    flat = ["src/a.py", "src/c.py"]

    selects = [s for s in traced(
        store, lambda: _rescore_overlay(store, LATEST, fresh_rows(), flat, CFG))
        if s.lstrip().upper().startswith("SELECT")]

    assert selects and all("i.path = " in s for s in selects), selects


# --- the output is byte identical ---------------------------------------------

def rendered(overlay, as_json: bool) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _rescore_json(overlay, LATEST) if as_json else _print_rescore_table(overlay, LATEST)
    return buf.getvalue()


def overlay_from(baseline: list[ScoredRow]):
    from crapkit.score import overlay_stale_coverage

    return overlay_stale_coverage(fresh_rows(), baseline, lane_scopes={"src", "ui"},
                                  target=CFG.target, scope_targets=CFG.scope_targets,
                                  cc_only_scopes=CFG.coverage_optional_scopes)


def test_the_rescore_table_is_byte_identical(store: SnapshotStore):
    flat = ["src/a.py", "src/c.py"]

    narrow = rendered(_rescore_overlay(store, LATEST, fresh_rows(), flat, CFG), as_json=False)

    assert narrow.count("\n") > 2, "the fixture has to print rows or this proves nothing"
    assert narrow == rendered(overlay_from(wide_read(store, flat)), as_json=False)


def test_the_rescore_json_is_byte_identical(store: SnapshotStore):
    flat = ["src/a.py", "src/c.py"]

    narrow = rendered(_rescore_overlay(store, LATEST, fresh_rows(), flat, CFG), as_json=True)

    assert json.loads(narrow)["functions"], "the fixture has to score something"
    assert narrow == rendered(overlay_from(wide_read(store, flat)), as_json=True)


# --- the budget ---------------------------------------------------------------

BIG_ROWS = 140_000
BIG_FILES = 2_800
BUDGET_MS = 500.0


@pytest.fixture(scope="module")
def big_store(tmp_path_factory) -> SnapshotStore:
    """A run the size of the flagship consumer's: 140,000 scored functions over
    2,800 files, which is where read_scored was measured at 799.5 ms."""
    rows = [ScoredRow("src", f"src/pkg{i % 40}/m{i % BIG_FILES}.py", f"f{i}( a )",
                      1 + (i % 50) * 7, 8 + (i % 50) * 7, 3, 3, 3, 5, 1, 1,
                      0.5, "measured", 4.0 + i % 13, "ok", 0)
            for i in range(BIG_ROWS)]
    st = SnapshotStore(tmp_path_factory.mktemp("big") / "crap.sqlite")
    st.write_run(commit="big", tool_versions={}, rows=rows, lanes={"unit": {"scopes": ["src"]}})
    return st


def test_the_baseline_read_for_two_files_stays_under_the_budget(big_store: SnapshotStore):
    """A `watch` cycle pays this per saved file. Half a second is the ceiling,
    not the target: the read measures under a millisecond, and anything near the
    budget means the whole-run scan came back."""
    flat = ["src/pkg0/m0.py", "src/pkg1/m1.py"]

    start = time.perf_counter()
    rows = _baseline_rows(big_store, 1, flat)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert rows, "the read has to find rows or the timing means nothing"
    assert len(rows) < BIG_ROWS // 100, f"{len(rows)} rows is not a per-file read"
    assert elapsed_ms < BUDGET_MS, f"baseline read took {elapsed_ms:.1f} ms"
