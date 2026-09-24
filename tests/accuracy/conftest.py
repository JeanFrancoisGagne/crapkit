"""Tier selection, the process-marker guard and the oracle fixture.

The hooks act on tests under tests/accuracy only: a session that also collects
tests/unit leaves those items as it found them.
"""
import itertools
import os
from pathlib import Path
import sys

import pytest

from accuracy.kit import corpus_run, oracles, repos, runlog, tiers

HERE = Path(__file__).resolve().parent
# Corpus files, probes and recordings are data: a test_*.py among them is a
# fixture's own test, never one of ours.
collect_ignore_glob = ["*/fixtures/*", "*/small/*", "*/recorded/*", "*/probes/*"]


def pytest_configure(config):
    for name, text in tiers.MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {text}")


def _ours(item) -> bool:
    return HERE in Path(str(item.path)).resolve().parents


def _platforms(item) -> list[str]:
    return [name for mark in item.iter_markers("platform") for name in mark.args]


def _runs(item, tier: str) -> bool:
    names = [mark.name for mark in item.iter_markers()]
    return tiers.selected(names, tier, _platforms(item))


def _dropped(items, tier: str) -> list:
    return [item for item in items if _ours(item) and not _runs(item, tier)]


def pytest_collection_modifyitems(config, items):
    dropped = _dropped(items, tiers.current_tier())
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        gone = set(map(id, dropped))
        items[:] = [item for item in items if id(item) not in gone]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if _ours(item):
        tiers.enter(mark.name for mark in item.iter_markers())
    try:
        yield
    finally:
        tiers.leave()


@pytest.fixture(scope="session")
def oracle():
    """oracle(name) is the installed, pin-checked tool a test reads its expected
    value from. A missing one fails the test; see kit/oracles.py."""
    tier = tiers.current_tier()
    return lambda name: oracles.require(name, tier)


@pytest.fixture(scope="session")
def repo_templates(tmp_path_factory):
    return repos.Templates(tmp_path_factory.mktemp("repo-templates"))


@pytest.fixture
def make_repo(repo_templates, tmp_path):
    """make_repo(spec) is a fresh copy of the spec's repo under this test's tmp_path."""
    numbers = itertools.count()
    return lambda spec: repo_templates.copy(spec, tmp_path / f"repo{next(numbers)}")


def pytest_sessionfinish(session, exitstatus):
    """Hand run.py the shape events the strategies emitted this session."""
    drawn = sys.modules.get("accuracy.kit.strategies")
    if drawn is not None and drawn.EVENTS:
        runlog.note("events", counts=dict(drawn.EVENTS))


def _shared_base(tmp_path_factory) -> Path:
    """The session's temp root every xdist worker shares."""
    base = tmp_path_factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


@pytest.fixture(scope="session")
def small_corpus(tmp_path_factory):
    """The small corpus, measured once per session: read its outputs, and take
    private_copy() before changing anything. See kit/corpus_run.py."""
    return corpus_run.measure(corpus_run.SMALL, _shared_base(tmp_path_factory),
                              corpus_run.date_now())
