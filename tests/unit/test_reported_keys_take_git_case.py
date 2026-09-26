"""A report's key takes the letter case git tracks the file in.

A root-relative key takes the case its directories list, so a runner that
wrote `PKG/mod.py` on a disk that ignores case joins git's `pkg/mod.py`. After
a case-only rename made without `git mv` (Explorer, Finder), the disk lists
`App.ts` while git still tracks `app.ts`: the listing then respelled the key
`src/app.ts` to a name git does not track, and the file's coverage and dark
lines joined nothing, though no byte of it moved. A key git tracks as written
keeps that spelling, and a key in another case takes the one git tracks.
"""
from __future__ import annotations

import os

from crapkit.repopath import Reported
from hand_scored_repo import make_repo


def _renamed_on_disk_only(tmp_path):
    root = make_repo(tmp_path / "repo", files={"src/app.ts": "export const a = 1;\n"})
    os.rename(root / "src" / "app.ts", root / "src" / "tmp_case.ts")
    os.rename(root / "src" / "tmp_case.ts", root / "src" / "App.ts")
    return root


def test_a_key_git_tracks_as_written_keeps_that_case_after_a_rename_on_disk(tmp_path):
    root = _renamed_on_disk_only(tmp_path)

    assert Reported(root).relative("src/app.ts") == "src/app.ts"


def test_a_key_in_another_case_takes_the_case_git_tracks(tmp_path):
    root = _renamed_on_disk_only(tmp_path)
    ignores_case = os.path.exists(root / "SRC" / "APP.TS")

    spelled = Reported(root).relative("SRC/APP.TS")

    assert spelled == ("src/app.ts" if ignores_case else "SRC/APP.TS")


def test_a_key_git_does_not_track_takes_the_case_the_disk_lists(tmp_path):
    root = make_repo(tmp_path / "repo", files={"src/app.ts": "export const a = 1;\n"})
    (root / "src" / "Extra.ts").write_text("export const b = 2;\n", encoding="utf-8")
    ignores_case = os.path.exists(root / "src" / "EXTRA.TS")

    spelled = Reported(root).relative("src/extra.ts")

    assert spelled == ("src/Extra.ts" if ignores_case else "src/extra.ts")
