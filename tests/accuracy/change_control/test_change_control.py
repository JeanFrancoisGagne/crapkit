"""The in-tree change-control rules on this checkout (T2 to T6, and where T1 lives).

These compare the committed goldens, lock and tables with each other and with
the collected tests, so they carry the change_control marker: they
guard against an unannounced move and are not an independent method. Until
kit-close runs `python tools/accuracy/change_control.py lock --initial`, the
lock, CHANGES.tsv, metric-digests.tsv and test-counts.tsv hold headers only,
and no file waits for the lock.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "tools" / "accuracy"
if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))
import change_control as cc  # noqa: E402

pytestmark = pytest.mark.change_control


def _report(problems) -> str:
    return cc.report(problems, [], "this checkout")


def test_the_lock_the_changes_the_changelog_and_the_metric_digests_agree():
    """T2 to T5."""
    tree = cc.DirTree(REPO)

    problems = cc.in_tree(tree, cc.running(tree))

    assert problems == [], _report(problems)


T1 = "tests/accuracy/corpus_goldens/test_goldens.py"


def test_golden_sets_come_with_the_test_that_measures_them_and_a_regenerator():
    """T1 lives with the goldens: test_goldens.py measures every set and compares
    it with the committed files, and regenerate.py is what `declare` runs."""
    tree = cc.DirTree(REPO)
    has_goldens = bool(cc.golden_tables(tree))

    assert not has_goldens or [T1, cc.REGENERATE] == [
        path for path in (T1, cc.REGENERATE) if (REPO / path).is_file()]
    assert not has_goldens or cc.small_goldens(tree) is not None



@pytest.mark.process
def test_the_committed_test_counts_are_the_collected_ones():
    """T6. Before the first lock the table holds no row."""
    tree = cc.DirTree(REPO)
    counts = cc.collect_counts(REPO)
    expected = counts if cc._initialized(tree) else {}

    assert cc.count_problems(tree.read(cc.COUNTS), expected) == []
    assert counts["change_control"] >= 40
