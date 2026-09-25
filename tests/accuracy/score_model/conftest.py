"""score-model's shared fixture: one measured corpus per session.

The small corpus when corpus-goldens has built it, else the kit's seed corpus,
measured through kit.corpus_run under the same lock and key every packet uses,
so a session measures each corpus once.
"""
import os

import pytest

from accuracy.kit import corpus_run


def measured_corpus_path():
    return corpus_run.SMALL if corpus_run.SMALL.is_dir() else corpus_run.SEED


@pytest.fixture(scope="session")
def scored_corpus(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    shared = base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    return corpus_run.measure(measured_corpus_path(), shared, corpus_run.date_now())
