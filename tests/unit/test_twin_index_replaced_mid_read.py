"""A stored twin index replaced while a reader holds it still answers in full.

The store keeps one run's index at a time: storing a newer run's drops every
older one. A `brief --batch` on run 7 can run for minutes while another session
verifies, lands run 8 and briefs it, and that brief replaces run 7's index. The
rest of the batch then looked its twins up in rows that were gone and reported
none, a plausible wrong answer. A reader now checks the index is still whole
after each lookup and, when it is not, answers that lookup and every later one
from a fresh build.
"""
import pytest

from crapkit.dup import find_duplicates, find_twins, function_index, twins_in
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore


def row(path, name, start, end):
    return InventoryRow(scope="src", path=path, long_name=name, start=start, end=end,
                        ccn_std=3, ccn_mod=3, ccn=3, nloc=end - start + 1, params=1, nesting=1)


BODY = "\n".join(f"    step_{i} = compute({i}) + offset" for i in range(10))
SOURCES = {"src/a.py": "def alpha():\n" + BODY + "\n",
           "src/b.py": "def beta():\n" + BODY + "\n",
           "src/c.py": "def gamma():\n" + BODY + "\n"}
ROWS = [row("src/a.py", "alpha", 1, 11), row("src/b.py", "beta", 1, 11),
        row("src/c.py", "gamma", 1, 11)]


@pytest.fixture()
def replaced(tmp_path):
    """Run 1's index held by a reader, then replaced by run 2's from another handle."""
    store = SnapshotStore(tmp_path / "crap.sqlite")
    first, second = (store.write_run(commit=c, tool_versions={}, rows=ROWS, kind="inventory")
                     for c in ("c1", "c2"))
    builds = []

    def build():
        builds.append(1)
        return function_index(ROWS, SOURCES)

    store.twin_index(first, build)
    held = SnapshotStore(tmp_path / "crap.sqlite").twin_index(first, build)
    assert twins_in(held, ROWS[0], SOURCES["src/a.py"]) == find_twins(ROWS[0], ROWS, SOURCES)
    SnapshotStore(tmp_path / "crap.sqlite").twin_index(second, lambda: function_index(ROWS, SOURCES))
    builds.clear()
    return held, builds


def test_twins_looked_up_after_the_index_was_replaced_are_all_there(replaced):
    held, builds = replaced

    for target in ROWS:
        assert twins_in(held, target, SOURCES[target.path]) == find_twins(target, ROWS, SOURCES)
    assert len(find_twins(ROWS[1], ROWS, SOURCES)) == 2
    assert builds == [1], "one rebuild answers every later lookup"


def test_pairs_counted_after_the_index_was_replaced_are_all_there(replaced):
    held, builds = replaced

    assert find_duplicates(ROWS, lambda: SOURCES, indexed=held) == \
        find_duplicates(ROWS, lambda: SOURCES)
    assert len(find_duplicates(ROWS, lambda: SOURCES)) == 3
    assert builds == [1]
