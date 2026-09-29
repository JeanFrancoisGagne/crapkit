"""Two measurements of the small corpus, in two directories and at two wall-clock
times, normalize to the same goldens.

A golden may hold only what a calculation decided. If a value that depends on
where or when the run happened (a temp path, a timestamp, a duration) slipped
past kit.surfaces.normalize, the two runs below would disagree on it, and so
would a golden check on any other machine.
"""
import time

import pytest

from accuracy.corpus_goldens import golden_runs
from accuracy.kit import corpus_run

# Nightly: on push, the golden checks on ubuntu and windows already compare runs made
# in other directories at other times with the committed goldens.
pytestmark = [pytest.mark.process, pytest.mark.nightly]


def _wait_past(stamp: float, seconds: float) -> None:
    """Return once the clock reads `seconds` past `stamp`, so every timestamp
    the second run prints differs from the first run's."""
    time.sleep(max(0.0, stamp + seconds - time.time()))


def test_two_runs_normalize_to_the_same_goldens(small_corpus, tmp_path):
    first = golden_runs.normalized(small_corpus)
    _wait_past((small_corpus.outputs.parent / corpus_run.MANIFEST).stat().st_mtime, 1.5)
    second_run = corpus_run.measure(corpus_run.SMALL, tmp_path / "elsewhere",
                                    corpus_run.date_now())

    assert second_run.root != small_corpus.root
    assert golden_runs.normalized(second_run) == first
