"""crap-typescript-core 0.5.2 as a CRAP oracle: its own calculateCrapScore, run by
node on the pinned package, at the (ccn, covered, total) inputs crapkit scores.

It imports no crapkit. The package lives under kit.oracles.node_modules("nightly").
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import hang_guard
from accuracy.kit import oracles

SCRIPT = Path(__file__).with_name("crap_typescript_scores.mjs")


def scores(cases: list[tuple[int, int, int]]) -> list[float]:
    """The tool's CRAP double for each (ccn, covered, total)."""
    node = shutil.which("node")
    if node is None:
        raise oracles.OracleMissing("node is not on PATH")
    done = hang_guard.run([node, str(SCRIPT), str(oracles.node_modules("nightly"))],
                          input=json.dumps(cases), text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)
