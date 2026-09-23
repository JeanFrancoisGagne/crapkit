"""The net under the Python reader refuses a def cut off in its signature, whichever reader cut it.

analyze.py refuses a file with a def the reader never read to its body, rather
than score that def at ccn 1 whatever its body holds. The net counts brackets
from the `def` keyword to the first `:` at depth 0, and the function current at
that colon has reached its body only if it owns the next token.

It has to hold under two readers. crapkit.lizardpython is registered today; the
stock lizard 1.24.0 reader is what analysis falls back to once that module
retires, and it cuts defs off in the shapes the net exists to catch. Before the
count started at `def`, it started at the first token the surviving function
owned, which under the stock reader is a `]` or a `:` for a PEP 695 def: depth
-1 refused a def read whole, and a `:` name token passed a def cut off at ccn 1.

Expected rows are (start, end, ccn), counted by hand from the source.
"""
import lizard_languages
import pytest
from lizard_languages.python import PythonReader as StockPythonReader

from crapkit.analyze import analyze_source
from crapkit.lizardpython import register
from crapkit.merge import UnanalyzableFile

# ccn 4: base 1, the `if`, the `for`, the nested `if`.
BODY = ("    if a:\n        return 1\n    for item in a:\n        if item:\n"
        "            return 2\n    return 0\n")
REFUSED = "refused"


def _indented(text: str) -> str:
    return "".join("    " + line + "\n" for line in text.splitlines())


def _verdict(source: str):
    records = analyze_source("net.py", source)
    if isinstance(records, UnanalyzableFile):
        return REFUSED
    return [(r.start, r.end, r.ccn) for r in records]


@pytest.fixture
def stock_reader():
    lizard_languages.PythonReader = StockPythonReader
    try:
        yield
    finally:
        register()


# Shapes lizard 1.24.0 cuts off: each would read as a def of two lines at ccn 1.
STOCK_CUTS_OFF = [
    ("line break after an empty tuple default",
     "def make(a, *, bases=(),\n         slots=False):\n" + BODY),
    ("return annotation opened on the def line",
     "def f(a) -> tuple[\n    int, int\n]:\n" + BODY),
    ("nested def with a line break after a default",
     "def outer(a):\n    def inner(a, b=(),\n              c=1):\n" + _indented(BODY)
     + "    return inner\n"),
    ("type parameters, then a line break after a default",
     "def f[T](a: T, b=(),\n      c=1):\n" + BODY),
    ("two bounds, then a line break after a default",
     "def f[T: (int, str), U: int](a, b=(),\n      c=1):\n" + BODY),
    ("end of file inside the signature", "def outer(a):\n    def inner(a, b=(),\n"),
]


@pytest.mark.parametrize("name, source", STOCK_CUTS_OFF, ids=[s[0] for s in STOCK_CUTS_OFF])
def test_the_stock_reader_cutting_a_def_off_refuses_the_file(stock_reader, name, source):
    assert _verdict(source) == REFUSED


# Generic defs the stock reader reads to their body under a `]` name. The name is
# the retirement pin's business; the net's is that the body was reached.
STOCK_READS_WHOLE = [
    ("method with a return annotation",
     "class Box:\n    def first[T](self) -> T:\n        if self.items:\n            return 1\n"
     "        return 0\n",
     [(2, 5, 2)]),
    ("no parameters", "def empty[T]() -> list[T]:\n    if flag:\n        return []\n    return list()\n",
     [(1, 4, 2)]),
    ("unannotated parameter", "def f[T](a):\n" + BODY, [(1, 7, 4)]),
    ("bound", "def f[T: int](a):\n" + BODY, [(1, 7, 4)]),
]


@pytest.mark.parametrize("name, source, expected", STOCK_READS_WHOLE, ids=[s[0] for s in STOCK_READS_WHOLE])
def test_the_stock_reader_reading_a_generic_def_whole_is_scored(stock_reader, name, source, expected):
    assert _verdict(source) == expected


def test_only_the_cut_off_def_is_named_when_its_parent_owns_the_rest_of_its_signature(stock_reader):
    """The stock reader ends `f` on line 4, which is indented less than line 3,
    and charges `int ,` to `outer`, which was read to its body before `f`
    began. Line 5 then ends `outer` too, before the colon."""
    source = ("def outer(a):\n    def f(value) -> tuple[\n            int,\n        int,\nint]:\n"
              "        if value:\n            return 1\n        return 0\n    return f\n")
    records = analyze_source("net.py", source)
    assert isinstance(records, UnanalyzableFile)
    assert "reached no body for 1 def(s): net.py:2 outer.f( value )" in records.reason
    assert "outer( a )" not in records.reason


# The corrected reader reads every generic def whole; only a file that ends
# inside a signature still has a def with no body.
CORRECTED = [
    ("end of file inside the parameter list", "def outer(a):\n    def inner(a, b=(),\n", REFUSED),
    ("end of file inside a generic def's parameter list",
     "def outer(a):\n    def inner[T](a, b=(),\n", REFUSED),
    ("end of file inside a type parameter list", "def outer(a):\n    def inner[T,\n", REFUSED),
    ("unannotated parameter", "def f[T](a):\n" + BODY, [(1, 7, 4)]),
    ("no parameters", "def empty[T]() -> list[T]:\n    if flag:\n        return []\n    return list()\n",
     [(1, 4, 2)]),
    ("type parameters, then a line break after a default",
     "def f[T](a: T, b=(),\n      c=1):\n" + BODY, [(1, 8, 4)]),
    ("two bounds, then a line break after a default",
     "def f[T: (int, str), U: int](a, b=(),\n      c=1):\n" + BODY, [(1, 8, 4)]),
]


@pytest.mark.parametrize("name, source, expected", CORRECTED, ids=[s[0] for s in CORRECTED])
def test_the_corrected_reader_is_refused_only_where_a_signature_has_no_body(name, source, expected):
    register()
    assert _verdict(source) == expected
