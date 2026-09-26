"""A message names a list of files or functions by its first three and a count
of the rest, and that rule lives in one module (crapkit.named).

It had grown five copies: the lane pages' sample, init's untracked source,
the worklist's changed files, prune's renames and verify's changed files and
debt, and one of them sorted what it was given while the others kept the
caller's order. The GitHub Action grew two more, in its comment's verdict line
and in the bash counter of its changed-files step; both run under the Python
the Action installs crapkit into, so both call it too.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from crapkit.named import first_few

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "crapkit"

CASES = {
    "none": ([], ""),
    "one": (["a.py"], "a.py"),
    "three": (["c.py", "a.py", "b.py"], "c.py, a.py, b.py"),
    "four": (["d.py", "c.py", "b.py", "a.py"], "d.py, c.py, b.py and 1 more"),
    "forty": ([f"f{i:02}.py" for i in range(40)], "f00.py, f01.py, f02.py and 37 more"),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_first_three_in_the_order_given_then_a_count(case):
    names, shown = CASES[case]

    assert first_few(names) == shown


def test_it_takes_any_iterable_of_names():
    assert first_few(iter(["b.py", "a.py"])) == "b.py, a.py"
    assert first_few({"x.py": 1}) == "x.py"


# The shape each copy spelled: src's one line that joins the shown names and
# counts the rest, the Action comment's tail and the Action step's bash counter.
_COPIES = (re.compile(r'f"\{shown\} and \{'),
           re.compile(r'" and \{[^}]*\} more"'),
           re.compile(r'" and \$\(\([^)]*\)\) more"'))


def _builds_the_sentence(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return any(copy.search(text) for copy in _COPIES)


def test_no_other_module_builds_the_sentence():
    """src, the Action's own Python and the Action's steps."""
    scanned = [*SRC.rglob("*.py"), *(ROOT / "tools").rglob("*.py"), ROOT / "action.yml"]

    homes = sorted(path.relative_to(ROOT).as_posix() for path in scanned if _builds_the_sentence(path))

    assert homes == ["src/crapkit/named.py"]
