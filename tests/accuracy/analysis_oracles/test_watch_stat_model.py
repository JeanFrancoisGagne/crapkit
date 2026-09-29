"""watch: crapkit.watch's snapshot and change set against a plain os.stat model.

A self-diff: it imports crapkit.watch, so test_watch.py, which drives the CLI,
is the calc's independent test. The model: a path's snapshot entry is os.stat's
st_mtime when the path is a regular file, and absent otherwise; a path changed
between two snapshots when its entry differs, appeared or went away.
"""
from __future__ import annotations

import os
from pathlib import Path

from hypothesis import given, strategies as st

from accuracy.kit.settings import pure


def _stat_model(root: Path, files: list[str]) -> dict[str, float]:
    out = {}
    for rel in files:
        try:
            info = os.stat(root / rel)
        except OSError:
            continue
        if os.path.isfile(root / rel):
            out[rel] = info.st_mtime
    return out


def test_snapshot_equals_a_plain_stat_walk(tmp_path):
    from crapkit import watch

    for path in ("a.py", "pkg/b.py", "pkg/deep/c.py", "Mixed.PY"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("x\n", encoding="utf-8")
    (tmp_path / "dir.py").mkdir()
    files = ["a.py", "pkg/b.py", "pkg/deep/c.py", "Mixed.PY", "gone.py", "dir.py", "pkg/gone/x.py"]
    assert watch.snapshot_mtimes(tmp_path, files) == _stat_model(tmp_path, files)


def _changed_model(before: dict, after: dict) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


SNAPSHOTS = st.dictionaries(st.sampled_from(["a.py", "b.py", "pkg/c.py", "d.ts"]),
                            st.sampled_from([1.0, 2.0, 3.5]))


@pure
@given(before=SNAPSHOTS, after=SNAPSHOTS)
def test_changed_paths_match_the_model(before, after):
    from crapkit import watch

    moved = watch.changed_paths(before, after)
    assert moved == _changed_model(before, after)
    assert set(moved) <= set(before) | set(after)
