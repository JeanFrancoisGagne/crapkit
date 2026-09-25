"""The seed corpus, measured once per session for every runtime-guards test."""
import os

import pytest

from accuracy.kit import corpus_run


@pytest.fixture(scope="session")
def seed(tmp_path_factory):
    """kit/fixtures/seed after its inventory, coverage and read-only surfaces:
    read its outputs, and take private_copy() before changing anything."""
    base = tmp_path_factory.getbasetemp()
    shared = base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    return corpus_run.measure(corpus_run.SEED, shared, corpus_run.DEFAULT_NOW)
