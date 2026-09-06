"""Stale coverage must stay with its same-line callback."""
import pytest

from crapkit.errors import ToolError
from crapkit.score import ScoredRow, overlay_stale_coverage
from crapkit.snapshot import InventoryRow


def inventory(occurrence, start=1, scope="src"):
    return InventoryRow(scope, "a.ts", "(anonymous)", start, start, 2, 2, 2,
                        1, 0, 0, occurrence=occurrence)


def baseline(occurrence, cov, start=1, scope="src"):
    return ScoredRow(scope, "a.ts", "(anonymous)", start, start, 2, 2, 2,
                     1, 0, 0, cov, "measured", 2.0, "ok", occurrence=occurrence)


@pytest.mark.parametrize("start", [1, 5])
def test_same_line_callbacks_keep_their_own_coverage_after_line_shift(start):
    rows = [inventory(1, start), inventory(2, start)]
    saved = [baseline(2, 1.0), baseline(1, 0.0)]
    result = overlay_stale_coverage(rows, saved, lane_scopes={"src"})
    assert [(r.occurrence, r.cov, r.flag) for r in result] == [
        (1, 0.0, "measured"), (2, 1.0, "measured")]


def test_legacy_duplicate_positions_are_refused():
    with pytest.raises(ToolError):
        overlay_stale_coverage([inventory(1), inventory(2)],
                               [baseline(0, 1.0), baseline(0, 0.0)], lane_scopes={"src"})


def test_legacy_scope_copies_are_not_a_second_callback():
    result = overlay_stale_coverage([inventory(1)],
                                   [baseline(0, 0.5), baseline(0, 0.5, scope="other")],
                                   lane_scopes={"src"})
    assert [(r.cov, r.flag) for r in result] == [(0.5, "measured")]


def test_new_sibling_cannot_inherit_legacy_unique_callback_coverage():
    result = overlay_stale_coverage([inventory(1), inventory(2)], [baseline(0, 1.0)],
                                   lane_scopes={"src"})
    assert [(r.cov, r.flag) for r in result] == [(0.0, "untested"), (0.0, "untested")]


def test_new_sibling_cannot_inherit_known_first_callback_coverage():
    result = overlay_stale_coverage([inventory(1), inventory(2)], [baseline(1, 1.0)],
                                   lane_scopes={"src"})
    assert [(r.cov, r.flag) for r in result] == [(1.0, "measured"), (0.0, "untested")]


def test_unique_signature_survives_other_names_added_on_its_line():
    result = overlay_stale_coverage([inventory(2)], [baseline(1, 0.75)], lane_scopes={"src"})
    assert [(r.cov, r.flag) for r in result] == [(0.75, "measured")]
