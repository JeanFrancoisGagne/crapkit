"""R120: `crapkit init` read testpaths from the first config file whose
testpaths was not empty, so a repo whose pytest.ini carries a `[pytest]`
section naming none got pyproject.toml's list, and init wrote lane stubs for
paths the repo's own `pytest` never collects.

    <retro venv python> R120.py WORKTREE

verdict_model's check judges the lane guard's rows, and the guard read
testpaths through its own loader at both commits: the check's rows read the
same at the commit before the fix and at the fix. The fix changed init's
reader, crapkit.scaffold.pytest_testpaths, which both commits have with one
signature. This probe hands it a pytest.ini with an empty `[pytest]` section and
a pyproject.toml naming two testpaths: the answer must be no testpaths.
"""
# source: pytest docs, "Initialization: determining rootdir and configfile": pytest.ini files take precedence over other files, even when empty, and only the first match is used; with no testpaths pytest collects from the rootdir
from __future__ import annotations

import sys

MARKERS = {"pytest.ini": "[pytest]\naddopts = -q\n",
           "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["conform", "impl"]\n'}


def main(argv: list[str]) -> int:
    from crapkit.scaffold import pytest_testpaths

    found = pytest_testpaths(MARKERS)
    assert found == (), f"init reads testpaths {found}; pytest reads pytest.ini alone, which names none"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
