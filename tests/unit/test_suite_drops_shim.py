"""`crapkit.lanes.suite_drops` keeps 0.8.0's path and signature for one release.

0.8.1 moved the suite-drop check into lane_results, where it walks the runs
behind this one instead of reading one run's lane provenance. A library caller
that imported the old name broke on the import alone. The old name stays through
0.8.x as a wrapper that warns, answers what lane_results answers for the same
last trusted run, and goes in 0.9.0.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crapkit import lanes
from crapkit.lane_results import suite_drops

UPGRADING = Path(__file__).resolve().parents[2] / "docs" / "upgrading.md"
PREVIOUS = {"py": {"tests_total": 20}}
CURRENT = {"py": {"tests_total": 12}}


def test_the_0_8_0_name_warns_and_answers_what_lane_results_answers():
    with pytest.warns(DeprecationWarning, match="lane_results.suite_drops"):
        notes = lanes.suite_drops(PREVIOUS, CURRENT)

    assert notes == suite_drops(lambda: [{"lanes": PREVIOUS}], CURRENT)
    assert notes == ["lane 'py' ran 12 tests, 8 fewer than the last trusted run's 20 - check "
                     "the runner's log for a worker that died without reporting it"]


@pytest.mark.parametrize("fraction, dropped", [(0.5, False), (0.1, True)])
def test_the_0_8_0_name_keeps_its_fraction_keyword(fraction, dropped):
    with pytest.warns(DeprecationWarning):
        notes = lanes.suite_drops(PREVIOUS, CURRENT, fraction=fraction)

    assert bool(notes) is dropped


def test_a_lane_the_last_run_never_counted_compares_nothing_through_the_old_name_too():
    """0.8.0 read a missing count as 0 tests; the old name answers by the 0.8.1 rule."""
    with pytest.warns(DeprecationWarning):
        assert lanes.suite_drops({"py": {}}, CURRENT) == []
        assert lanes.suite_drops(PREVIOUS, {"py": {"exit_code": 0}}) == []


def test_the_upgrade_guide_names_the_old_name_and_what_replaces_it():
    text = " ".join(UPGRADING.read_text(encoding="utf-8").split())

    assert "`crapkit.lanes.suite_drops(previous, current)`" in text
    assert "`crapkit.lane_results.suite_drops(behind, current)`" in text
    assert "0.9.0" in text
