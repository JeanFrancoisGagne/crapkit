"""Tests that break a session guard on purpose. They are deselected unless
CRAPKIT_ACCURACY_GUARD_PROBES is set, which only test_kit_guards does, in a
pytest session of its own whose write guard watches a directory of its own."""
import os
from pathlib import Path

import pytest

from accuracy.kit import guards

pytestmark = pytest.mark.guard_probe


def test_probe_skips():
    pytest.skip("a probe that skips")


@pytest.mark.xfail(strict=True)
def test_probe_xfails_without_a_ruling():
    raise AssertionError("a probe that fails")


def test_probe_writes_where_the_guard_watches():
    watched = Path(os.environ[guards.ROOT_ENV])
    (watched / "guard-probe-written.txt").write_text("written by a probe\n", encoding="utf-8")


def test_probe_passes():
    """The control: a probe that breaks nothing passes."""
