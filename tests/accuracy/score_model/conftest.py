"""score-model's shared fixtures: one measured corpus per session, and the
crapkit modules the tests load by name, imported once before any test runs,
with Hypothesis's scan of their constants done then too.

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
    read_local_constants()


def read_local_constants():
    """Hypothesis parses the source of every imported local module the first
    time a draw asks for a constant (providers._get_local_constants), inside
    that draw. With crapkit and the tests imported that parse takes 1 to 5
    seconds, and the too_slow health check then fails whichever test drew
    first (three different tests in one Windows nightly). Parsing here, after
    the imports, keeps it out of every draw; later imports add only their own
    modules. A Hypothesis without that function has nothing to warm."""
    with contextlib.suppress(ImportError, AttributeError):
        from hypothesis.internal.conjecture import providers

        providers._get_local_constants()


def measured_corpus_path():
    return corpus_run.SMALL if corpus_run.SMALL.is_dir() else corpus_run.SEED


@pytest.fixture(scope="session")
def scored_corpus(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    shared = base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    return corpus_run.measure(measured_corpus_path(), shared, corpus_run.date_now())
