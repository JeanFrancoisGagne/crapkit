"""A parameter default that opens a bracket or a lambda keeps its def and every def after it.

lizard 1.24.2 hands a default that opens with `(`, `{` or `lambda` to a state of
its own, `PythonStates._state_default_value`, which reads the default to its
closing bracket. crapkit.lizardpython counts bracket depth only in the states it
overrides, so it counted the default's `(` and never saw its `)`. The reader
then waited for a body colon at depth 0 that never came: no line after it set a
nesting level, lizard listed neither that def nor any def after it in the file,
and nothing refused the file. `pyproject.toml` admits lizard 1.24.0 to 1.24.x,
and a fresh install takes the newest.

Each shape below is the def under test, then `h`, which holds the same body.
The names are lizard 1.24.0's spelling, which stops at the signature's first `)`
and is what the ratchet keys on (tests/unit/test_lizardpython.py, slice 2). The
ccn is counted by hand, and every span must equal ast's. Under lizard 1.24.0
these read the same before and after the fix; under 1.24.2 and later every shape
marked RED read as no function at all.
"""
import ast

import pytest

from crapkit.analyze import analyze_source

# ccn 4: base 1, the `if`, the `for`, the nested `if`.
BODY = ("    if a:\n        return 1\n    for item in a:\n        if item:\n"
        "            return 2\n    return 0\n")
AFTER = "\n\ndef h(a):\n" + BODY
H = ("h( a )", 4)


def _indented(text: str) -> str:
    return "".join("    " + line + "\n" for line in text.splitlines())


def _ast_spans(source: str) -> list[tuple[int, int]]:
    defs = (ast.FunctionDef, ast.AsyncFunctionDef)
    return sorted((n.lineno, n.end_lineno) for n in ast.walk(ast.parse(source)) if isinstance(n, defs))


# (id, source, [(long_name, ccn)]). RED: no rows under lizard 1.24.2 or later before the fix.
SHAPES = [
    ("RED empty tuple", "def f(a=()):\n" + BODY + AFTER, [("f( a = ( )", 4), H]),
    ("RED empty dict", "def f(a={}):\n" + BODY + AFTER, [("f( a = { } )", 4), H]),
    ("RED one-element tuple", "def f(a=(1,)):\n" + BODY + AFTER, [("f( a = ( 1 , )", 4), H]),
    ("RED annotated, with a return annotation",
     "def f(a: int, b: tuple[int, ...] = ()) -> None:\n" + BODY + AFTER,
     [("f( a : int , b : tuple [ int , ... ] = ( )", 4), H]),
    ("RED lambda whose body opens a bracket",
     "def f(key=lambda item: (item, 1)):\n" + BODY + AFTER,
     [("f( key = lambda item : ( item , 1 )", 4), H]),
    ("RED call with a keyword", "def f(a=field(default_factory=list)):\n" + BODY + AFTER,
     [("f( a = field ( default_factory = list )", 4), H]),
    ("RED empty call", "def f(a=list()):\n" + BODY + AFTER, [("f( a = list ( )", 4), H]),
    ("RED dict holding a tuple", 'def f(a={"k": (1, 2)}):\n' + BODY + AFTER,
     [('f( a = { "k" : ( 1 , 2 )', 4), H]),
    ("RED walrus", "def f(a=(b := 1)):\n" + BODY + AFTER, [("f( a = ( b := 1 )", 4), H]),
    ("RED nested brackets in a call", 'def f(a=type("C", (), {})):\n' + BODY + AFTER,
     [('f( a = type ( "C" , ( )', 4), H]),
    ("RED subscripted tuple", "def f(a=(1, 2)[0:1]):\n" + BODY + AFTER,
     [("f( a = ( 1 , 2 )", 4), H]),
    ("RED string holding a bracket, then a tuple", 'def f(a="(", b=()):\n' + BODY + AFTER,
     [('f( a = "(" , b = ( )', 4), H]),
    ("RED async def", "async def f(a=()):\n" + BODY + AFTER, [("f( a = ( )", 4), H]),
    ("RED exploded signature", "def f(\n    a,\n    b={},\n) -> int:\n" + BODY + AFTER,
     [("f( a , b = { } , )", 4), H]),
    ("RED body on the colon line", "def f(a=()): return a\n" + AFTER, [("f( a = ( )", 1), H]),
    ("RED method", "class C:\n    def f(self, a=()):\n" + _indented(BODY)
     + "\n    def h(self, a):\n" + _indented(BODY),
     [("f( self , a = ( )", 4), ("h( self , a )", 4)]),
    ("RED nested def", "def outer(a):\n    def f(a=()):\n" + _indented(BODY) + "    return f\n" + AFTER,
     [("outer.f( a = ( )", 4), ("outer( a )", 1), H]),
    ("list default", "def f(a=[]):\n" + BODY + AFTER, [("f( a = [ ] )", 4), H]),
    ("strings holding brackets", "def f(a=\"(\", b=\")\"):\n" + BODY + AFTER,
     [('f( a = "(" , b = ")" )', 4), H]),
    ("decorator with brackets", "@deco(1, (2,))\ndef f(a):\n" + BODY + AFTER, [("f( a )", 4), H]),
]


@pytest.mark.parametrize("name, source, expected", SHAPES, ids=[s[0] for s in SHAPES])
def test_a_bracketed_default_keeps_its_def_and_the_defs_after_it(name, source, expected):
    records = analyze_source("defaults.py", source)
    assert [(r.long_name, r.ccn) for r in records] == expected
    assert sorted((r.start, r.end) for r in records) == _ast_spans(source)
