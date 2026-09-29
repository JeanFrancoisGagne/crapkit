"""R169: the pytest-cov probe took a lane's first word for its interpreter, so
for `coverage run -m pytest --cov=pylib && coverage json` it ran `coverage -c
"import pytest_cov"`, read coverage's own usage error as a missing package, and
said pytest_cov was missing where it imports.

    <retro venv python> R169.py WORKTREE

verdict_model's check asks doctor about such a lane, and at the commit before
the fix only init ran the probe, on lanes init writes, none of which start with
`coverage run`, so no CLI path reached the bug and the check passes there. This
probe asks the function the fix changed, crapkit.cli.admin._pytest_cov_probe,
which both commits have, about that lane, with this venv's scripts first on
PATH, where pytest_cov imports: the answer must be that it can.
"""
# requires: pytest==9.1.1 pytest-cov==7.1.0 coverage==7.16.1
# source: coverage.py's command line (coverage.readthedocs.io, "Command line usage"): `coverage run -m pytest` runs pytest in the python coverage is installed in, here this venv, where `import pytest_cov` succeeds (checked below before the probe is asked)
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

LANE = "coverage run -m pytest --cov=pylib && coverage json"


def main(argv: list[str]) -> int:
    if importlib.util.find_spec("pytest_cov") is None:
        raise RuntimeError("pytest_cov does not import here, so the probe can tell nothing")
    os.environ["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), os.environ.get("PATH", "")))
    from crapkit.cli.admin import _pytest_cov_probe

    assert _pytest_cov_probe(LANE), f"the probe says pytest_cov is missing for {LANE!r}, where it imports"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
