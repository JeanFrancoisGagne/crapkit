"""The Python reader where a def's lines are not its indentation, every row hand-counted.

The Python Language Reference, 2.1.6 "Implicit line joining": inside
parentheses, brackets or braces a statement goes on over lines, and "the
indentation of the continuation lines is not important". So a line inside
brackets neither opens nor closes a block, whatever its column, and a body on
the colon line runs to the bracket that closes it. A default's nested brackets
are part of the parameter list, which ends at the `)` that closes the def's
own `(`.

Each row is (long name, start line, end line, ccn); the long name keeps
lizard's spelling, the key a ratchet mark was recorded under.
"""
import pytest

from crapkit.analyze import analyze_source

G = "def g(a):\n    if a:\n        return 1\n    return 0\n"


def rows(code: str) -> list[tuple]:
    return [(r.long_name, r.start, r.end, r.ccn_std) for r in analyze_source("src/a.py", code)]


@pytest.mark.parametrize("code, expected", [
    # a default holding brackets two deep: the signature still ends at its own `)`
    ("def f(a=((1,),)):\n    if a:\n        return 1\n    return 0\n",
     [("f( a = ( ( 1 , )", 1, 4, 2)]),
    # a body on the colon line that opens a bracket runs to the line that closes it
    ("def f(): (\n    print(1)\n)\n\n\n" + G, [("f( )", 1, 3, 1), ("g( a )", 6, 9, 2)]),
    # a closing bracket at column 0 inside a body closes no block
    ("def f(a):\n    x = g(\n        a,\n)\n    if a:\n        return 1\n    return 0\n",
     [("f( a )", 1, 7, 2)]),
])
def test_a_line_inside_brackets_is_no_block_boundary(code, expected):
    assert rows(code) == expected


def test_a_def_that_ends_a_colon_line_body_can_sit_left_of_it():
    """lizard reads the format spec `(>10` as an open bracket; the `def` on the
    next line, at column 0 below a method, still ends that method's body."""
    code = 'class A:\n    def f(self): return f"{self:(>10}"\ndef g():\n    return 1\n'

    assert [row[:3] for row in rows(code)] == [("f( self )", 2, 2), ("g( )", 3, 4)]
