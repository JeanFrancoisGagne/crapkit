"""The in-tree change-control rules on this checkout (T1 to T6).

These compare crapkit's committed goldens, lock and tables with each other and
with crapkit's current output, so they carry the change_control marker: they
guard against an unannounced move and are not an independent method. Until
kit-close runs `python tools/accuracy/change_control.py lock --initial`, the
lock, CHANGES.tsv, metric-digests.tsv and test-counts.tsv hold headers only,
and no file waits for the lock.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

from accuracy.kit import corpus_run, goldens

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


def _stored(directory: Path) -> dict[str, str]:
    return {path.name: path.read_bytes().decode("utf-8") for path in directory.iterdir()
            if path.is_file()}


def _differences(directory: Path, now: dict[str, str]) -> list[str]:
    stored = _stored(directory)
    return [f"{directory.relative_to(REPO).as_posix()}/{name}: differs from crapkit's output"
            for name in sorted({*stored, *now}) if stored.get(name) != now.get(name)]


def _measured(request, corpus: str):
    """The session's shared run of the small corpus, or a run of its own."""
    if corpus == cc.SMALL_CORPUS:
        return request.getfixturevalue("small_corpus")
    base = request.getfixturevalue("tmp_path_factory").mktemp("golden-set")
    return corpus_run.measure(REPO / corpus, base, corpus_run.date_now())


@pytest.mark.golden
@pytest.mark.process
def test_every_golden_set_equals_crapkit_output(request):
    """T1, for every golden set whose corpus is in the tree; the full corpus's sets
    are compared where the full corpus is (the nightly image)."""
    sets = cc._in_tree_sets(cc.DirTree(REPO))

    found = [line for directory, corpus in sets for line in _differences(
        REPO / directory, goldens.goldens_of(_measured(request, corpus)))]

    assert found == [], "\n".join(found + [cc.declare_command("<id>", "<kind>", ())])


def test_a_small_corpus_has_its_golden_set():
    corpus = cc.DirTree(REPO)
    has_corpus = any(path.startswith(cc.SMALL_CORPUS + "/") for path in corpus.paths())

    assert not has_corpus or cc.small_goldens(corpus) is not None


@pytest.mark.process
def test_the_committed_test_counts_are_the_collected_ones():
    """T6. Before the first lock the table holds no row."""
    tree = cc.DirTree(REPO)
    counts = cc.collect_counts(REPO)
    expected = counts if cc._initialized(tree) else {}

    assert cc.count_problems(tree.read(cc.COUNTS), expected) == []
    assert counts["change_control"] >= 40
