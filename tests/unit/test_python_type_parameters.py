"""A Python def with a PEP 695 type parameter list reads under its own name.

lizard 1.24.0's PythonReader names a def after the last token before its `(`,
so `def f[T](a: int):` reads as `]( a : int )` and `def f[T: (int, str)](a):`
as `:( int , str )`. That name is the ratchet key, it collides with every other
generic def in the file, the cognitive pass reads each `]` in the body as a call
to the function itself, and a def whose name token sits on a later line than
`def` starts on that line.

crapkit.lizardpython reads the type parameter list as part of the signature:
the def is named by its name token and keeps the parameter list lizard spells
today. Every expectation below was counted by hand from the source, and the
spans are checked against `ast` end lines on an interpreter that parses PEP 695.
"""
import ast
import sys

import pytest

from crapkit.analyze import analyze_source

# ccn 4: base 1, the `if`, the `for`, the nested `if`.
BODY = ("    if a:\n        return 1\n    for item in a:\n        if item:\n"
        "            return 2\n    return 0\n")


def _indented(text: str) -> str:
    return "".join("    " + line + "\n" for line in text.splitlines())


def _read(source: str):
    return [(r.long_name, r.start, r.end, r.ccn) for r in analyze_source("generic.py", source)]


SHAPES = [
    ("annotated parameter", "def f[T](a: int):\n" + BODY,
     [("f( a : int )", 1, 7, 4)]),
    ("unannotated parameter", "def f[T](a):\n" + BODY,
     [("f( a )", 1, 7, 4)]),
    ("method with a return annotation",
     "class Box:\n    def first[T](self) -> T:\n        if self.items:\n            return 1\n"
     "        return 0\n",
     [("first( self )", 2, 5, 2)]),
    ("no parameters", "def empty[T]() -> list[T]:\n    if flag:\n        return []\n    return list()\n",
     [("empty( )", 1, 4, 2)]),
    ("bound", "def f[T: int](a):\n" + BODY,
     [("f( a )", 1, 7, 4)]),
    ("constrained bound", "def f[T: (int, str)](a):\n" + BODY,
     [("f( a )", 1, 7, 4)]),
    ("default (PEP 696)", "def f[T = int](a):\n" + BODY,
     [("f( a )", 1, 7, 4)]),
    ("variadic and parameter spec", "def f[*Ts, **P](a):\n" + BODY,
     [("f( a )", 1, 7, 4)]),
    ("type parameter list over several lines", "def f[\n    T,\n    U,\n](a):\n" + BODY,
     [("f( a )", 1, 10, 4)]),
    ("async def", "async def f[T]() -> T:\n" + BODY,
     [("f( )", 1, 7, 4)]),
    ("decorated def", "@cache\ndef f[T]() -> T:\n" + BODY,
     [("f( )", 2, 8, 4)]),
    ("line break after an empty tuple default (#72)", "def f[T](a: T, b=(),\n      c=1):\n" + BODY,
     [("f( a : T , b = ( )", 1, 8, 4)]),
    # lizard read this one as `:( int , str )` over lines 1-2 at ccn 1.
    ("two bounds and a line break after a default",
     "def f[T: (int, str), U: int](a, b=(),\n      c=1):\n" + BODY,
     [("f( a , b = ( )", 1, 8, 4)]),
    ("return annotation opened on the def line", "def f[T](a) -> tuple[\n    T, T\n]:\n" + BODY,
     [("f( a )", 1, 9, 4)]),
    ("nested def",
     "def outer(a):\n    def inner[T](x: T) -> T:\n" + _indented(BODY) + "    return inner\n",
     [("outer.inner( x : T )", 2, 8, 4), ("outer( a )", 1, 9, 1)]),
]


@pytest.mark.parametrize("name, source, expected", SHAPES, ids=[s[0] for s in SHAPES])
def test_a_generic_def_reads_under_its_own_name_over_its_whole_span(name, source, expected):
    assert _read(source) == expected


@pytest.mark.skipif(sys.version_info < (3, 12), reason="ast parses PEP 695 from Python 3.12")
@pytest.mark.parametrize("name, source, expected", SHAPES, ids=[s[0] for s in SHAPES])
def test_the_hand_counted_spans_match_ast(name, source, expected):
    defs = (ast.FunctionDef, ast.AsyncFunctionDef)
    spans = sorted((n.lineno, n.end_lineno) for n in ast.walk(ast.parse(source)) if isinstance(n, defs))
    assert sorted((start, end) for _, start, end, _ in expected) == spans


def test_a_subscript_in_the_body_is_not_a_call_to_the_def():
    """Cognitive 1, the `if`. lizard's `]` name made every `a[0]` read as recursion."""
    (record,) = analyze_source("generic.py", "def f[T](a: list[T]) -> T:\n    if a:\n"
                                             "        return a[0]\n    return a\n")
    assert (record.long_name, record.cognitive) == ("f( a : list [ T ] )", 1)


def test_a_call_by_the_defs_own_name_is_recursion():
    """Cognitive 2: the `if`, and `f(...)` read as direct recursion."""
    (record,) = analyze_source("generic.py", "def f[T](a: T) -> T:\n    if a:\n"
                                             "        return f(a)\n    return a\n")
    assert (record.long_name, record.cognitive) == ("f( a : T )", 2)


def test_two_generic_defs_with_the_same_parameters_are_not_twins(capsys):
    source = "def first[T](a: T):\n    return a\n\n\ndef second[T](a: T):\n    return a\n"
    assert [r.long_name for r in analyze_source("generic.py", source)] == ["first( a : T )", "second( a : T )"]
    assert "more than once" not in capsys.readouterr().err
