"""Near-duplicate twins: a newer run's index lands while `brief --batch` reads (R54).

The store keeps one run's twin index at a time, and storing a newer run's drops
the older one. A `brief --batch` that holds run 1's index while another session
stores run 2's must still list every twin in every packet (README `brief`:
`--batch N` output is byte-identical to N separate calls).

The seam is crapkit's store, so this module imports crapkit and is not the
calc's independent test (test_duplicates.py is). The expected twins are hand
values: three copies of one 10-line body under three names. Each function is
11 normalized lines, so 8 shingles of 4 lines; the def lines differ, so 7 are
shared, and each packet lists the other two at 7/8 = 0.875.

The race is forced with no thread and no sleep, at one of three moments:
- `reader`: right after `SnapshotStore.twin_index` hands brief the stored index;
- `lookup`: inside the first packet's lookup, after its postings query and
  before its rows query;
- `packet`: after the first packet's lookup, before the second packet's.
The other session is a second SnapshotStore handle that writes run 2 and
stores its index, as a `coverage` and a `brief` in another terminal do.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from accuracy.analysis_oracles import analysis_inventory
from accuracy.kit import drive, repos

pytestmark = pytest.mark.process

NOW = repos.EPOCH + 86_400  # a day after the kit's commit date: inside the churn window
# ccn 8 (for, if, elif and four boolean operators), over the config's target 6
# with no coverage lane, so each copy is an actionable queue item
BODY = ["total = 0", "for item in items:", "    if item > limit and item % 2 and item != 7:",
        "        total += item * 2", "    elif item < 0 or item > 100 or item == 50:",
        "        total -= item", "    else:", "        total += 1", "count = len(items)",
        "return total / max(count, 1)"]
NAMES = {"a.py": "twin_a", "b.py": "twin_b", "c.py": "twin_c"}
EXPECTED = {name: {other: 0.875 for other in NAMES.values() if other != name}
            for name in NAMES.values()}


def _function(name: str) -> str:
    return f"def {name}(items, limit):\n" + "".join(f"    {line}\n" for line in BODY)


def _indexed_runs(root: Path) -> list[int]:
    """The runs whose twin index the store holds, read with sqlite3."""
    rows = drive.Driver(root).store("SELECT run_id FROM twin_runs ORDER BY run_id")
    return [row["run_id"] for row in rows]


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    """Run 1 scored and its twin index stored, as the first brief on a run leaves it."""
    files = {path: _function(name) for path, name in NAMES.items()}
    tree = {"crapkit.toml": analysis_inventory.config(analysis_inventory.languages_of(files)),
            **files}
    root = analysis_inventory.build(tree, tmp_path_factory.mktemp("r54") / "repo")
    driver = drive.Driver(root, date_now=NOW)
    for args in (("coverage",), ("brief", "a.py", "twin_a", "--json")):
        done = driver.run(*args)
        assert done.code == 0, done.stderr
    assert _indexed_runs(root) == [1]
    return root


def _land_run_two(root: Path, store_index) -> None:
    """Another session writes run 2 and stores its index, which drops run 1's.
    `store_index` is SnapshotStore.twin_index as crapkit defines it. The handle
    is not closed: SnapshotStore.close is newer than R54's fix, and every write
    here has committed."""
    from crapkit.dup import function_index
    from crapkit.store import SnapshotStore

    other = SnapshotStore(root / drive.STORE)
    rows = other.read_rows(1)
    sources = {path: (root / path).read_text(encoding="utf-8") for path in NAMES}
    run = other.write_run(commit="0" * 40, tool_versions={}, rows=rows, kind="inventory")
    store_index(other, run, lambda: function_index(rows, sources))


def _after_reader(monkeypatch, land) -> None:
    from crapkit.store import SnapshotStore

    handed = SnapshotStore.twin_index

    def racing(store, run_id, build):
        index = handed(store, run_id, build)
        land()
        return index

    monkeypatch.setattr(SnapshotStore, "twin_index", racing)


def _postings_asked(seen: list, sql: str) -> int:
    """This query's number among the batch's postings queries; 0 for any other query."""
    if "twin_postings" not in sql:
        return 0
    seen.append(sql)
    return len(seen)


def _before_postings_query(monkeypatch, land, number: int) -> None:
    """Land just before the batch's `number`th postings query runs."""
    from crapkit.store import _StoredTwins

    queried = _StoredTwins._chunked
    seen: list = []

    def racing(reader, sql, values):
        if _postings_asked(seen, sql) == number:
            land()
        yield from queried(reader, sql, values)

    monkeypatch.setattr(_StoredTwins, "_chunked", racing)


def _after_postings_query(monkeypatch, land, number: int) -> None:
    """Land as soon as the batch's `number`th postings query has read its last row."""
    from crapkit.store import _StoredTwins

    queried = _StoredTwins._chunked
    seen: list = []

    def racing(reader, sql, values):
        asked = _postings_asked(seen, sql)
        yield from queried(reader, sql, values)
        if asked == number:
            land()

    monkeypatch.setattr(_StoredTwins, "_chunked", racing)


MOMENTS = {
    "reader": _after_reader,
    "lookup": lambda monkeypatch, land: _after_postings_query(monkeypatch, land, 1),
    "packet": lambda monkeypatch, land: _before_postings_query(monkeypatch, land, 2),
}


def _once(action):
    started = []

    def land():
        if not started:
            started.append(True)
            action()

    return land


def _twins(payload: dict) -> dict:
    return {analysis_inventory.bare(packet["function"]):
            {analysis_inventory.bare(twin["long_name"]): twin["similarity"]
             for twin in packet["duplication_twins"]}
            for packet in payload["packets"]}


@pytest.mark.parametrize("moment", sorted(MOMENTS))
def test_brief_twins_survive_a_concurrent_store(stored, moment, monkeypatch, tmp_path):
    """R54: every packet of the batch lists its two twins at 0.875 however the
    newer index lands. The batch runs in this process, whatever
    CRAPKIT_ACCURACY_PYTHON says, because the race lives in its store calls."""
    from crapkit.store import SnapshotStore

    root = Path(shutil.copytree(stored, tmp_path / "repo"))
    store_index = SnapshotStore.twin_index
    land = _once(lambda: _land_run_two(root, store_index))
    MOMENTS[moment](monkeypatch, land)
    monkeypatch.delenv(drive.PYTHON_ENV, raising=False)
    done = drive.Driver(root, date_now=NOW).run("brief", "--batch", "3", "--json")
    monkeypatch.undo()
    assert _indexed_runs(root) == [2], "the race never ran"
    assert (done.code, done.json()["run_id"]) == (0, 1), done.stderr
    assert _twins(done.json()) == EXPECTED
