"""Controls: what 0.8.0 said about a touched or edited tree after a lane measured it.

test_lane_source_digests.py pins these rows through crapkit.uncovered.lane_views,
which 0.8.1 introduced, so on 0.8.0 each of them stops at an AttributeError
before its verdict. Each test here replays one row through readers 0.8.0
already had (the reuse warning a coverage run prints, and the uncovered-lines
note), so it passes on 0.8.0 and on every later tree.
"""
from __future__ import annotations

import pytest

import stale_tree
from stale_tree import EVENTS, REL
from test_lane_source_digests import SUBMODULE_EVENTS, _prepared, _reuse_warning, _with_submodule


@pytest.mark.parametrize("name", ["submodule-touch", "submodule-touch-norefresh"])
def test_a_touch_inside_a_submodule_moves_nothing(name, tmp_path, capsys):
    root = _with_submodule(tmp_path)
    act, _ = SUBMODULE_EVENTS[name]
    act(root)

    assert _reuse_warning(root, capsys) == ""


def test_an_edit_inside_a_submodule_counts_one_changed_file(tmp_path, capsys):
    """0.8.0 counted the change; naming src/vendor came with 0.8.1."""
    root = _with_submodule(tmp_path)
    act, _ = SUBMODULE_EVENTS["submodule-edit"]
    act(root)

    assert "1 file(s) in its scopes changed since" in _reuse_warning(root, capsys)


def test_a_touch_withholds_no_uncovered_line(tmp_path, monkeypatch):
    from crapkit.uncovered import load_uncovered

    assert "touch" in EVENTS
    root, truth = _prepared("touch", tmp_path, monkeypatch)
    lines = load_uncovered(root, stale_tree.config(root))

    assert not truth
    assert lines.note_for(REL) == ""
