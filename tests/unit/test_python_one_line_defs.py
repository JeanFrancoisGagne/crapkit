"""A Python def whose body sits on its colon line is listed like the same def on two lines.

lizard 1.24.0 lists a def when a later line pushes a nesting level for it, and
`def f(x): return x` pushes none: the def is never listed, and it stays pending
on the nesting stack. The lines after it at its own indent are charged to it,
so an enclosing def loses them, and the next line indented deeper pushes it,
so a later def carries its name: `test_autospec.g.a( self )`.

The reader ends such a def with the logical line that holds its body. Every
expected row is (long_name, start, end, ccn, nloc, params), counted by hand
from the source: a one-line def starts and ends on its def line, counts the
conditions on that line, and holds one line of code.
"""
import ast

import lizard_languages
import pytest
from lizard_languages.python import PythonReader as StockPythonReader

from crapkit.analyze import analyze_source
from crapkit.keys import key_names
from crapkit.lizardpython import register
from crapkit.merge import UnanalyzableFile


def _read(source: str):
    return [(r.long_name, r.start, r.end, r.ccn, r.nloc, r.params) for r in analyze_source("oneline.py", source)]


def _ast_names(source: str) -> list[tuple[str, int, int]]:
    """(enclosing-def chain, lineno, end_lineno) for every def; a class adds nothing."""
    out = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((".".join(prefix + [child.name]), child.lineno, child.end_lineno))
                walk(child, prefix + [child.name])
            else:
                walk(child, prefix)

    walk(ast.parse(source), [])
    return sorted(out, key=lambda row: row[1])


def test_a_one_line_def_is_listed_as_its_own_function():
    source = "def one_line(x): return x\n\n\ndef g(y):\n    return y\n"
    assert _read(source) == [("one_line( x )", 1, 1, 1, 1, 1), ("g( y )", 4, 5, 1, 2, 1)]


# Each shape twice: body on the colon line, and the same def with its body on
# the next line, which lizard already lists. Name, start, ccn and params agree;
# the one-line reading ends a line earlier and holds one line fewer.
SAME_DEF = [
    ("stub",
     "def f(): ...\n", [("f( )", 1, 1, 1, 1, 0)],
     "def f():\n    ...\n", [("f( )", 1, 2, 1, 2, 0)]),
    ("conditions on the body line",
     "def f(x, y): return 1 if x and y else 2\n", [("f( x , y )", 1, 1, 3, 1, 2)],
     "def f(x, y):\n    return 1 if x and y else 2\n", [("f( x , y )", 1, 2, 3, 2, 2)]),
    ("async def",
     "async def f(x): return await x\n", [("f( x )", 1, 1, 1, 1, 1)],
     "async def f(x):\n    return await x\n", [("f( x )", 1, 2, 1, 2, 1)]),
    ("overload stubs before the implementation",
     "@overload\ndef f(a: int) -> int: ...\n@overload\ndef f(a: str) -> str: ...\ndef f(a):\n    return a\n",
     [("f( a : int )", 2, 2, 1, 1, 1), ("f( a : str )", 4, 4, 1, 1, 1), ("f( a )", 5, 6, 1, 2, 1)],
     "@overload\ndef f(a: int) -> int:\n    ...\n@overload\ndef f(a: str) -> str:\n    ...\ndef f(a):\n"
     "    return a\n",
     [("f( a : int )", 2, 3, 1, 2, 1), ("f( a : str )", 5, 6, 1, 2, 1), ("f( a )", 7, 8, 1, 2, 1)]),
    ("methods of a class",
     "class A:\n    def f(self): return 1\n    def g(self): return 2\n",
     [("f( self )", 2, 2, 1, 1, 1), ("g( self )", 3, 3, 1, 1, 1)],
     "class A:\n    def f(self):\n        return 1\n    def g(self):\n        return 2\n",
     [("f( self )", 2, 3, 1, 2, 1), ("g( self )", 4, 5, 1, 2, 1)]),
    ("exploded parameters",
     "class S:\n    def __init__(\n        self,\n        match: str | None,\n    ) -> None: ...\n",
     [("__init__( self , match : str | None , )", 2, 5, 1, 4, 2)],
     "class S:\n    def __init__(\n        self,\n        match: str | None,\n    ) -> None:\n        ...\n",
     [("__init__( self , match : str | None , )", 2, 6, 1, 5, 2)]),
]


@pytest.mark.parametrize("name, one_line, one_line_rows, two_lines, two_line_rows", SAME_DEF,
                         ids=[s[0] for s in SAME_DEF])
def test_a_body_on_the_colon_line_reads_as_the_same_def_on_two_lines(
        name, one_line, one_line_rows, two_lines, two_line_rows):
    assert _read(two_lines) == two_line_rows
    assert _read(one_line) == one_line_rows


def test_the_enclosing_def_keeps_the_lines_after_a_one_line_def():
    """lizard charged `if y: return 1` and `return 2` to `f` and listed no `outer`."""
    source = "def outer():\n    def f(x): return x\n    if y: return 1\n    return 2\n"
    assert _read(source) == [("outer.f( x )", 2, 2, 1, 1, 1), ("outer( )", 1, 4, 2, 4, 0)]


def test_a_comment_on_the_line_or_after_it_leaves_the_one_line_def_ending_with_its_line():
    source = ("def outer():\n    def f(x): return x  # the whole body\n    # outer resumes\n"
              "    if y: return 1\n    return 2\n")
    assert _read(source) == [("outer.f( x )", 2, 2, 1, 1, 1), ("outer( )", 1, 5, 2, 4, 0)]


def test_a_def_after_a_one_line_def_keeps_its_own_name():
    """The shape from the stdlib's unittest.mock tests that read `test_autospec.g.a( self )`."""
    source = '''class T:
    def test_autospec(self):
        class Boo(object):
            def __init__(self, a): pass
            def f(self, a): pass
            def g(self): pass
            foo = 'bar'

            class Bar(object):
                def a(self): pass

        def _test(mock):
            mock(1)
'''
    rows = [(name.split("(")[0].strip(), start, end) for name, start, end, *_ in _read(source)]
    assert sorted(rows, key=lambda row: row[1]) == _ast_names(source) == [
        ("test_autospec", 2, 13), ("test_autospec.__init__", 4, 4), ("test_autospec.f", 5, 5),
        ("test_autospec.g", 6, 6), ("test_autospec.a", 10, 10), ("test_autospec._test", 12, 13)]


def test_a_one_line_def_before_a_class_does_not_take_its_methods():
    """lizard pushed the pending `f` for the class body and read `f.g( self )`, `f` over lines 1-4."""
    source = "def f(x): return x\nclass A:\n    def g(self):\n        return 1\n"
    assert _read(source) == [("f( x )", 1, 1, 1, 1, 1), ("g( self )", 3, 4, 1, 2, 1)]


def test_a_body_that_runs_over_lines_inside_brackets_ends_with_its_logical_line():
    source = "def f(x): return [\n    x,\n        x,\n]\ny = 1\n\n\ndef g():\n    return 1\n"
    assert _read(source) == [("f( x )", 1, 4, 1, 4, 1), ("g( )", 8, 9, 1, 2, 0)]


def test_a_body_after_a_backslash_continuation_ends_with_its_logical_line():
    source = "def f(x): \\\n    return x\ny = 1\n\n\ndef g():\n    return 1\n"
    assert _read(source) == [("f( x )", 1, 2, 1, 2, 1), ("g( )", 6, 7, 1, 2, 0)]


def test_a_one_line_def_on_the_last_line_without_a_newline_is_listed():
    assert _read("class A:\n    def f(self): return 1") == [("f( self )", 2, 2, 1, 1, 1)]


# --- the unread-def net keeps its answers for a one-line def, under both readers -----


@pytest.fixture
def stock_reader():
    lizard_languages.PythonReader = StockPythonReader
    try:
        yield
    finally:
        register()


CUT_BY_STOCK = "def f(a) -> tuple[\n    int, int\n]: return (1, 1)\n\n\ndef g():\n    return 1\n"


def test_a_one_line_def_the_stock_reader_cuts_off_in_its_signature_refuses_the_file(stock_reader):
    """The stock reader ends `f` on line 2, before the `]:` its body follows."""
    records = analyze_source("oneline.py", CUT_BY_STOCK)
    assert isinstance(records, UnanalyzableFile)
    assert "reached no body for 1 def(s): oneline.py:1 f( a )" in records.reason


def test_the_corrected_reader_reads_that_def_to_its_body_on_the_colon_line():
    assert _read(CUT_BY_STOCK) == [("f( a )", 1, 3, 1, 3, 1), ("g( )", 6, 7, 1, 2, 0)]


def test_one_line_overload_stubs_take_the_twin_keys_their_two_line_form_takes(capsys):
    one_line = "@overload\ndef f(a) -> int: ...\n@overload\ndef f(a) -> str: ...\ndef f(a):\n    return a\n"
    two_lines = ("@overload\ndef f(a) -> int:\n    ...\n@overload\ndef f(a) -> str:\n    ...\ndef f(a):\n"
                 "    return a\n")
    for source in (one_line, two_lines):
        records = analyze_source("oneline.py", source)
        assert sorted(key_names(records).values()) == ["f( a )", "f( a )#2", "f( a )#3"]
        assert "oneline.py defines f( a ) more than once" in capsys.readouterr().err
