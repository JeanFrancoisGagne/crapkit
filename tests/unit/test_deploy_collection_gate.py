"""tests/deploy runs only when CRAPKIT_DEPLOY=1 asks for it.

The deploy cells install crapkit through every channel a user has and drive
real harness CLIs. They need the pinned toolchain the images or
tools/deploy/toolchain.py provide, so a bare `pytest` on a contributor's
machine skips the whole tree instead of failing on a missing Node or a
harness that is not installed.
"""
from pathlib import Path

import conftest

TESTS = Path(conftest.__file__).resolve().parent


def test_a_bare_run_ignores_the_deploy_tree(monkeypatch):
    monkeypatch.delenv("CRAPKIT_DEPLOY", raising=False)

    assert conftest.pytest_ignore_collect(TESTS / "deploy" / "test_kit_isolation.py", None) is True


def test_the_deploy_switch_collects_it(monkeypatch):
    monkeypatch.setenv("CRAPKIT_DEPLOY", "1")

    assert conftest.pytest_ignore_collect(TESTS / "deploy" / "test_kit_isolation.py", None) is None


def test_other_trees_are_left_to_pytest(monkeypatch):
    monkeypatch.delenv("CRAPKIT_DEPLOY", raising=False)

    assert conftest.pytest_ignore_collect(TESTS / "unit" / "test_config.py", None) is None
