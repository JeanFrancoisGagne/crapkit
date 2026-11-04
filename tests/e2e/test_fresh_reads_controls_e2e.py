"""Controls: what the readers said about an unchanged or merely touched tree in 0.8.0.

test_scored_changes_e2e.py and test_git_view_readers_e2e.py pin these rows with
the fields 0.8.1 added (`scored_changes`, `changed_paths`). 0.8.0's JSON has
neither, so on 0.8.0 those tests stop at a KeyError before their verdict. Each
test here replays one row and asserts only the fields 0.8.0 printed, so it
passes on 0.8.0 and on every later tree:

- next-item after nothing changed, and after a touch that kept the bytes:
  `stale` is false and the queue hands out the same function.
- brief after a touch in a lane scope: `stale` is false and the source is the
  committed text.
- verify with one file committed and another touched while git's index was
  not refreshed: one changed file, and its breach is committed, not dirty.
"""
from __future__ import annotations

import json

import pytest

from test_git_view_readers_e2e import (REL, _touch_norefresh, committed_repo, git, plain, settle,
                                       tangled)
from test_git_view_readers_e2e import run_cli as run_view_cli
from test_scored_changes_e2e import _json, _measure, _nothing, _py_repo, _touch_app, _ts_repo, \
    _ts_touch


@pytest.mark.parametrize("change", [_nothing, _touch_app], ids=["clean", "touch"])
def test_next_item_is_fresh_after_nothing_or_a_touch(tmp_path, change):
    repo = _py_repo(tmp_path)
    _measure(repo)
    before = _json(repo, "next-item")["item"]
    change(repo)

    out = _json(repo, "next-item")

    assert (out["stale"], out["item"]) == (False, before)


def test_brief_is_fresh_after_a_touch_in_a_lane_scope(tmp_path):
    repo = _ts_repo(tmp_path, cc_only=False)
    committed = (repo / "src" / "app.ts").read_text(encoding="utf-8")
    _ts_touch(repo)

    out = _json(repo, "brief", "src/app.ts", "dispatch", "--json")

    assert out["stale"] is False
    assert out["source"] in committed


def test_verify_counts_one_changed_file_when_another_is_only_touched(tmp_path):
    repo = committed_repo(tmp_path, "", {REL: plain("keep")})
    assert run_view_cli(repo, "coverage").returncode == 0
    (repo / REL).write_text(plain("keep") + "\n\n" + tangled("route"), encoding="utf-8")
    git(repo, "commit", "-q", "-am", "route over the ceiling")
    settle(repo, "src/other.py")
    _touch_norefresh(repo, "src/other.py")

    res = run_view_cli(repo, "verify", "--no-tighten", "--json")

    payload = json.loads(res.stdout)
    assert payload["changed_files"] == 1, payload
    assert [(g["path"], g["long_name"].split("(")[0], g["dirty"])
            for g in payload["findings"] if g["kind"] == "gate_violation"] == [(REL, "route", False)], payload
