"""Tier selection, the process-marker guard and the oracle fixture.

The hooks act on tests under tests/accuracy only: a session that also collects
tests/unit leaves those items as it found them.
"""
from pathlib import Path

import pytest

from accuracy.kit import tiers

HERE = Path(__file__).resolve().parent


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
