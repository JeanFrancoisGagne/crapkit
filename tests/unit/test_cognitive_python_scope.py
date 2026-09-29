"""A Python comprehension's level lasts as long as its bracket, and a line
inside a bracket starts no statement.

Sonar Cognitive Complexity v1.7 charges a structure +1 and the nesting it sits
in. The pass kept a Python level per indent, closed by the next line at the
same indent, so the level a comprehension's `for` opened stayed open to the end
of its line: `[p for p in a] + [q for q in b]` charged the second `for` one
level deeper than the first and read cognitive 3, nesting 2. A comprehension's
level now closes with its bracket.

A line that continues a bracket starts no statement either. An `if` or `else`
at the start of such a line is a ternary's, and its indent closes no block:
reading it as a statement charged a ternary's `else` a point, and a
continuation line indented less than the block around it closed that block
early.
"""
import pytest

from crapkit.analyze import analyze_source


def _row(source: str):
    rows = analyze_source("a.py", source, note=False)
    assert len(rows) == 1, [r.long_name for r in rows]
    return rows[0]


CASES = [  # (label, source, cognitive, nesting)
    ("two sibling comprehensions", "def f(a, b):\n    return [p for p in a] + [q for q in b]\n", 2, 1),
    ("a generator in a call, then a list", "def f(a, b):\n    return sum(p for p in a), [q for q in b]\n",
     2, 1),
    ("a dict comprehension, then a loop on the same line", "def f(a):\n    x = {k: 1 for k in a}; "
     "y = [z for z in a]\n    return x, y\n", 2, 1),
    ("a nested comprehension still nests", "def f(m):\n    return [x for row in m for x in row]\n", 3, 2),
    # The element before a comprehension's `for` sits outside its loop, as a
    # conditional expression there does (crapkit's reading; complexipy nests both).
    ("a comprehension as another's element sits outside its loop",
     "def f(m):\n    return [[x for x in row] for row in m]\n", 2, 1),
    ("a statement's body after a comprehension in its header",
     "def f(xs, y):\n    if any(x for x in xs):\n        if y:\n            return 1\n    return 0\n", 5, 2),
    ("a comprehension over several lines", "def f(a, b):\n    return [\n        p\n        for p in a\n"
     "    ] + [\n        q\n        for q in b\n    ]\n", 2, 1),
    ("an if on a comprehension's continuation line is its filter",
     "def f(a):\n    return [x\n            for x in a\n            if x]\n", 3, 1),
    ("the filter reads the same on one line", "def f(a):\n    return [x for x in a if x]\n", 3, 1),
    ("a ternary over lines costs one", "def f(a, b):\n    return (a\n            if b\n"
     "            else None)\n", 1, 0),
    ("a continuation line indented less keeps the block open",
     "def f(a, b):\n    if a:\n        x = g(1,\n    2)\n        if b:\n            return x\n", 3, 2),
    ("a statement loop after a comprehension line", "def f(a):\n    x = [p for p in a]\n"
     "    for q in x:\n        if q:\n            return q\n", 4, 2),
    ("async for is a statement loop", "async def f(a):\n    async for x in a:\n        if x:\n"
     "            return x\n", 3, 2),
]


@pytest.mark.parametrize("label,source,cognitive,nesting", CASES, ids=[c[0] for c in CASES])
def test_a_comprehension_level_closes_with_its_bracket(label, source, cognitive, nesting):
    row = _row(source)
    assert (row.cognitive, row.nesting) == (cognitive, nesting)


def test_a_dict_brace_closes_no_block():
    """A brace is a bracket in Python. Read as a block's brace, the `}` of a
    dict nested as deep as the indent of an open block closed that block."""
    source = ("def f(a, b):\n"
              "    if a:\n"
              "        x = {1: {2: {3: {4: {5: 6}}}}}\n"
              "        if b:\n"
              "            return x\n")
    row = _row(source)
    assert (row.cognitive, row.nesting) == (3, 2)
