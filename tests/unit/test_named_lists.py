"""A message names a list of files or functions by its first three and a count
of the rest, and that rule lives in one module (crapkit.named).

It had grown five copies: the lane pages' sample, init's untracked source,
the worklist's changed files, prune's renames and verify's changed files and
debt, and one of them sorted what it was given while the others kept the
caller's order.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from crapkit.named import first_few

SRC = Path(__file__).resolve().parents[2] / "src" / "crapkit"

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


# The shape each copy spelled, one line that joins the shown names and counts
# the rest.
_COPY = re.compile(r'f"\{shown\} and \{')


def test_no_other_module_builds_the_sentence():
    homes = sorted(path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py")
                   if _COPY.search(path.read_text(encoding="utf-8")))

    assert homes == ["named.py"]
