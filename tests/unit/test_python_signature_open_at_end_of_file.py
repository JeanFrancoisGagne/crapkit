"""A Python file that ends inside a def's signature is refused, whichever reader read it.

The unread-def net refuses a file with a def read no further than its
signature. It finds such a def in lizard's function list, and lizard lists a
def only when something pops it off the nesting stack. A def whose signature
the file ends inside is still pending: it was listed only when an enclosing def
was popped at the end of the file, because lizard lists the current function
there, not the popped one. A top-level def, or one in a class, was never
listed, so the file scored as if the def were not there.

The net now lists the def whose signature is still open when the tokens run
out, so it is named like any other def read no further than its signature.
"""
import lizard_languages
import pytest
from lizard_languages.python import PythonReader as StockPythonReader

from crapkit.analyze import analyze_source
from crapkit.lizardpython import register
from crapkit.merge import UnanalyzableFile


@pytest.fixture(params=["corrected", "stock"])
def either_reader(request):
    if request.param == "stock":
        lizard_languages.PythonReader = StockPythonReader
    try:
        yield
    finally:
        register()


# (source, line, the def's own name). The stock reader still prefixes the
# case after a one-line def and a class with that def, `f.g( ...`: that is its
# pin in tests/unit/test_lizardpython.py, not the net's business.
#
# The stock reader cuts the last two off at the return annotation's `[` and
# charges the rest of the signature to the file's global pseudo function. That
# is no def, and the net names none for it.
OPEN_AT_END = [
    ("top-level def", "def g(a, b=(),\n", 1, "g( a , b = ( )"),
    ("method of a class", "class A:\n    def g(self, b=(),\n", 2, "g( self , b = ( )"),
    ("after a one-line def", "def f(x): return x\ndef g(a, b=(),\n", 2, "g( a , b = ( )"),
    ("after a one-line def and a class", "def f(x): return x\nclass A:\n    def g(self, b=(),\n",
     3, "g( self , b = ( )"),
    ("nested def", "def outer(a):\n    def inner(a, b=(),\n", 2, "outer.inner( a , b = ( )"),
    ("top-level return annotation", "def f(a) -> tuple[\n    int,\n]", 1, "f( a )"),
    ("method return annotation", "class A:\n    def f(self) -> tuple[\n        int,\n    ]", 2, "f( self )"),
]


@pytest.mark.parametrize("name, source, line, def_name", OPEN_AT_END, ids=[s[0] for s in OPEN_AT_END])
def test_a_file_ending_inside_a_signature_is_refused_and_names_that_def(either_reader, name, source, line,
                                                                         def_name):
    records = analyze_source("open.py", source)
    assert isinstance(records, UnanalyzableFile)
    assert f"reached no body for 1 def(s): open.py:{line} " in records.reason
    assert f"{def_name}; a def read no further" in records.reason
    assert "*global*" not in records.reason


def test_a_file_ending_on_the_def_keyword_names_no_def(either_reader):
    """No name token, so no function: the file scores what it holds."""
    records = analyze_source("open.py", "def f(a):\n    return a\ndef")
    assert [(r.long_name, r.start, r.end) for r in records] == [("f( a )", 1, 2)]
