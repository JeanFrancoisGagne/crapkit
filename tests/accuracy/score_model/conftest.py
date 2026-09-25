"""score-model's shared fixtures: one measured corpus per session, and the
crapkit modules the tests load by name, imported once before any test runs.

The corpus is the small corpus when corpus-goldens has built it, else the kit's
seed corpus, measured through kit.corpus_run under the same lock and key every
packet uses, so a session measures each corpus once.
"""
import contextlib
import importlib
import os

import pytest

from accuracy.kit import corpus_run

# The modules production.load reaches. A Hypothesis example that paid for the
# first import of crapkit.cli.queue took 295 ms on a Docker Desktop bind mount,
# past the 200 ms deadline, so the session imports them before any example runs.
# A retro replay against an older crapkit lacks some of them; that test then
# fails on its own load, not here.
WARM = ("score", "worklist", "cli.queue", "packet", "digest", "doctor", "config", "junitparse",
        "lanes", "churn", "coverage_istanbul", "coverage_py", "snapshot", "store")


@pytest.fixture(scope="session", autouse=True)
def warm_crapkit_imports():
    for name in WARM:
        with contextlib.suppress(ImportError):
            importlib.import_module(f"crapkit.{name}")


def measured_corpus_path():
    return corpus_run.SMALL if corpus_run.SMALL.is_dir() else corpus_run.SEED


@pytest.fixture(scope="session")
def scored_corpus(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    shared = base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    return corpus_run.measure(measured_corpus_path(), shared, corpus_run.date_now())
