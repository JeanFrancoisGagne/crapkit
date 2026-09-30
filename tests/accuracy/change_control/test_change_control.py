"""The in-tree change-control rules on this checkout (T2 to T6, and where T1 lives).

These compare the committed goldens, lock and tables with each other and with
the collected tests, so they carry the change_control marker: they
guard against an unannounced move and are not an independent method. Until
kit-close runs `python tools/accuracy/change_control.py lock --initial`, the
lock, CHANGES.tsv, metric-digests.tsv and test-counts.tsv hold headers only,
and the lockable files wait for the lock.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from accuracy.change_control import cc_seeds
from accuracy.kit.test_kit_contract import KIT_CLOSED

REPO = Path(__file__).resolve().parents[3]
cc = cc_seeds.tool()


pytestmark = pytest.mark.change_control


def _report(problems) -> str:
    return cc.report(problems, [], "this checkout")


def _before_the_first_lock(problems) -> list:
    """Until kit-close, the one problem allowed: lockable files wait for the lock."""
    return [problem for problem in problems if problem.fix == f"{cc.TOOL} lock --initial"]


def test_the_lock_the_changes_the_changelog_and_the_metric_digests_agree():
    """T2 to T5. Before kit-close sets KIT_CLOSED, files may wait for the first
    lock; after it, the lock holds every lockable file."""
    tree = cc.DirTree(REPO)

    problems = cc.in_tree(tree, cc.running(tree))

    allowed = [] if KIT_CLOSED or cc._initialized(tree) else _before_the_first_lock(problems)
    assert problems == allowed, _report(problems)


def _names_its_release(change: dict, versions: set[str]) -> bool:
    cell = change.get("changelog", "")
    return cell == "" if change.get("kind") == "none" else cell in versions


def test_each_change_names_the_release_whose_changelog_section_holds_its_line():
    """A change of a kind other than none names the version of the CHANGELOG.md
    section its `(accuracy change Cn)` line sits in; kind none needs no line
    (T4) and names none."""
    tree = cc.DirTree(REPO)
    log = (REPO / cc.CHANGELOG).read_bytes().decode("utf-8")
    versions = set(re.findall(r"^## (\d+\.\d+\.\d+) ", log, re.MULTILINE))

    wrong = {key: change.get("changelog") for key, change in cc.changes_of(tree).items()
             if not _names_its_release(change, versions)}

    assert wrong == {}


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
