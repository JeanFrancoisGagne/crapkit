"""Analysis cache: an edit that lands while crapkit hashes the file (R30).

The seam is crapkit's own content hash, so this module imports crapkit and is
not the calc's independent test (test_cache_identity.py is). The oracle is the
same as there: a cold analysis of the bytes the file holds afterwards.
"""
from __future__ import annotations

import pytest

from accuracy.analysis_oracles import analysis_inventory
from accuracy.analysis_oracles.test_cache_identity import SMALL, Warm, _edit, cold

pytestmark = pytest.mark.process


# --- an edit inside the hash call (R30) ------------------------------------------------------------

def test_edit_during_analysis_publishes_the_new_digest(monkeypatch, tmp_path):
    """The seam is crapkit's content hash: the wrapper hashes the old bytes and
    then writes new ones, as an editor saving mid-run would. Whatever the raced
    run answers, the next run must read the new bytes, as a cold run does."""
    import crapkit.analyze as analyze

    warm = Warm(SMALL, tmp_path / "warm")
    warm.run()
    edited = _edit(SMALL["a.py"], 7)
    hashed = analyze.content_hash

    def racing(path):
        digest = hashed(path)
        if path.name == "a.py" and path.read_text(encoding="utf-8") != edited:
            path.write_text(edited, encoding="utf-8")
        return digest

    warm.write("a.py", SMALL["a.py"] + "\n")
    monkeypatch.setattr(analyze, "content_hash", racing)
    raced = analysis_inventory.run_inventory(warm.root, tmp_path / "raced.tsv")
    monkeypatch.undo()
    assert (warm.root / "a.py").read_text(encoding="utf-8") == edited, "the race never ran"
    warm.files["a.py"] = edited
    after = cold(dict(warm.files), tmp_path / "cold")
    before = cold({**warm.files, "a.py": SMALL["a.py"] + "\n"}, tmp_path / "before")
    assert raced.code != 0 or raced.rows in (before.rows, after.rows)
    assert warm.run().rows == after.rows
