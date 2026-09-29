"""Analysis cache: an edit that lands while crapkit hashes the file (R30).

The seam is crapkit's own content hash, crapkit.analyze.content_hash. The
launch code below wraps it inside the crapkit process the kit spawns, so the
race runs where the analysis runs, also in a retro replay, which spawns every
call. That name exists at e064046 and d3a2137, the commits R30 names, and
today. The check names a crapkit internal, so it is not the calc's independent
test (test_cache_identity.py is). The oracle is the same as there: a cold
analysis of the bytes the file holds.
"""
from __future__ import annotations

import pytest

from accuracy.analysis_oracles import analysis_inventory
from accuracy.analysis_oracles.test_cache_identity import SMALL, Warm, _edit, cold

pytestmark = pytest.mark.process

RACE_ENV = "CRAPKIT_RACE_BYTES"
# Hash a.py, then write the bytes of the file RACE_ENV names over it once, as an
# editor saving mid-run would, then run the CLI. Three files stay below the
# analysis pool, so the hash runs in this process.
RACING = ("-c", "\n".join((
    "import os, sys",
    "from pathlib import Path",
    "import crapkit.analyze as analyze",
    "hashed = analyze.content_hash",
    f"new = Path(os.environ['{RACE_ENV}']).read_bytes()",
    "def racing(path):",
    "    digest = hashed(path)",
    "    if path.name == 'a.py' and path.read_bytes() != new:",
    "        path.write_bytes(new)",
    "    return digest",
    "analyze.content_hash = racing",
    "from crapkit.cli import main",
    "sys.exit(main(sys.argv[2:]))")))


# --- an edit inside the hash call (R30) ------------------------------------------------------------

def test_edit_during_analysis_publishes_the_new_digest(tmp_path):
    """Whatever the raced run answers, it must not file the edit's records under
    the old bytes' hash: with the old bytes back, a warm run reads what a cold
    run of them reads. With the edit back, a warm run reads the edit."""
    warm = Warm(SMALL, tmp_path / "warm")
    warm.run()
    old, edited = SMALL["a.py"] + "\n", _edit(SMALL["a.py"], 7)
    source = tmp_path / "edited.py"
    source.write_bytes(edited.encode("utf-8"))
    warm.write("a.py", old)
    raced = analysis_inventory.run_inventory(warm.root, tmp_path / "raced.tsv", launch=RACING,
                                             env={RACE_ENV: str(source)})
    if (warm.root / "a.py").read_bytes() != source.read_bytes():
        pytest.fail(f"the race never ran (exit {raced.code}): {raced.stderr}")
    before = cold({**warm.files, "a.py": old}, tmp_path / "before")
    after = cold({**warm.files, "a.py": edited}, tmp_path / "after")
    assert raced.code != 0 or raced.rows in (before.rows, after.rows)
    warm.write("a.py", old)
    assert warm.run().rows == before.rows
    warm.write("a.py", edited)
    assert warm.run().rows == after.rows
