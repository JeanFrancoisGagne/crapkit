"""Where crapkit's own source tree is, for the checks that read it as data.

The calc mutation stage (tools/accuracy/mutation.py) names its checkout's
src/crapkit in CRAPKIT_ACCURACY_SOURCE: mutmut's copy of src/ under mutants/
holds a trampoline for every function and every mutant's body beside the
original, so a check that reads crapkit's source as text reads the stage
checkout's, which is the same commit as written. Unset, it is this checkout's.
No crapkit import.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENV = "CRAPKIT_ACCURACY_SOURCE"


def root() -> Path:
    """crapkit's source tree as written: the directory ENV names, else src/crapkit."""
    named = os.environ.get(ENV)
    return Path(named) if named else REPO / "src" / "crapkit"
