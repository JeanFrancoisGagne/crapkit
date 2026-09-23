"""A Python def whose signature runs past lizard's idea of its end reads whole (#72).

lizard 1.24.0 ends a def inside its own signature in two shapes, and the def then
reads as two lines at ccn 1 whatever its body holds:

  * a return annotation opened on the def line (`-> tuple[` ... `]:`)
  * a line break after a parameter default that holds brackets (`bases=(),`),
    which is what black and ruff write for a long signature

crapkit.lizardpython reads both to the body's colon. Every expectation in the
"reads whole" tables was measured once with stock lizard 1.24.0 through
analyze_source and pasted in, never computed in the test. The corrected tables
were counted by hand and checked against `ast` end lines.

test_stock_reader_still_cuts_the_issue_def_off is the retirement signal: it
fails on the lizard release that reads these signatures.
test_stock_reader_still_names_a_generic_def_after_a_bracket is the same signal
for PEP 695 type parameter lists (tests/unit/test_python_type_parameters.py),
and test_stock_reader_still_repeats_the_outer_names_three_deep for the name of a
def nested three deep (tests/unit/test_python_nested_def_names.py).
src/crapkit/lizardpython.py goes, with its register() call, once all three fail.
"""
import ast

import lizard
import lizard_languages
import pytest
from lizard_languages.python import PythonReader as StockPythonReader

from crapkit.analyze import analyze_files, analyze_source
from crapkit.merge import UnanalyzableFile
from crapkit.lizardpython import PythonSignatureReader, register

# ccn 4: base 1, the `if`, the `for`, the nested `if`.
BODY = ("    if a:\n        return 1\n    for item in a:\n        if item:\n"
        "            return 2\n    return 0\n")


def _indented(text: str) -> str:
    return "".join("    " + line + "\n" for line in text.splitlines())


# The three functions of issue #72, verbatim. Hand count: ccn 4 each.
ISSUE = '''def annotation_opens_on_the_def_line(value: int) -> tuple[
    int, int
]:
    if value > 0:
        return 1, 1
    if value < 0:
        return -1, -1
    for _ in range(3):
        value += 1
    return 0, 0


def annotation_closes_on_one_line(value: int) -> tuple[int, int]:
    if value > 0:
        return 1, 1
    if value < 0:
        return -1, -1
    for _ in range(3):
        value += 1
    return 0, 0


def parameters_split_instead(
    value: int,
) -> tuple[int, int]:
    if value > 0:
        return 1, 1
    if value < 0:
        return -1, -1
    for _ in range(3):
        value += 1
    return 0, 0
'''


def _read(source: str, name: str = "shape.py"):
    return [(r.long_name, r.start, r.end, r.ccn) for r in analyze_source(name, source)]


def _ast_spans(source: str) -> list[tuple[int, int]]:
    defs = (ast.FunctionDef, ast.AsyncFunctionDef)
    return sorted((n.lineno, n.end_lineno) for n in ast.walk(ast.parse(source)) if isinstance(n, defs))


# --- slice 1: the issue's three functions -----------------------------------------


def test_the_issue_functions_all_read_ccn_4_over_their_whole_span():
    assert _read(ISSUE, "issue72.py") == [
        ("annotation_opens_on_the_def_line( value : int )", 1, 10, 4),
        ("annotation_closes_on_one_line( value : int )", 13, 20, 4),
        ("parameters_split_instead( value : int , )", 23, 32, 4),
    ]


def test_stock_reader_still_cuts_the_issue_def_off():
    """The upstream defect, pinned. Fails the day lizard reads the signature whole."""
    lizard_languages.PythonReader = StockPythonReader
    try:
        stock = lizard.analyze_file.analyze_source_code("issue72.py", ISSUE).function_list
    finally:
        register()
    assert [(f.name, f.start_line, f.end_line, f.cyclomatic_complexity) for f in stock][0] == (
        "annotation_opens_on_the_def_line", 1, 2, 1)


def test_stock_reader_still_names_a_generic_def_after_a_bracket():
    """The PEP 695 half, pinned: `def f[T](a: int):` reads as `]`, not `f`.
    Fails the day lizard names the def by its name token."""
    lizard_languages.PythonReader = StockPythonReader
    try:
        stock = lizard.analyze_file.analyze_source_code("generic.py", "def f[T](a: int):\n" + BODY)
    finally:
        register()
    assert [(f.name, f.long_name) for f in stock.function_list] == [("]", "]( a : int )")]


def test_stock_reader_still_repeats_the_outer_names_three_deep():
    """The nested-name half, pinned: a def three deep reads `a.a.b.c`, not
    `a.b.c`. Fails the day lizard names each enclosing def once."""
    lizard_languages.PythonReader = StockPythonReader
    try:
        stock = lizard.analyze_file.analyze_source_code(
            "nested.py", "def a(x):\n    def b(y):\n        def c(z):\n            return z\n"
                         "        return c\n    return b\n")
    finally:
        register()
    assert [f.name for f in stock.function_list] == ["a.a.b.c", "a.b", "a"]


# --- slice 2: the shapes lizard cut off read their whole span and real ccn ----------

# long_name keeps lizard's spelling, which stops at the signature's first ')'
# whatever its depth: the ratchet keys on it, and the same spelling is what
# lizard gives a def of this shape that it already read whole.
CUT_OFF = [
    ("empty tuple default, then a break",
     "def make(a, *, bases=(),\n         slots=False):\n" + BODY,
     [("make( a , * , bases = ( )", 1, 8, 4)]),
    ("frozenset() default",
     "def f(a, skip=frozenset(),\n      strict=False):\n" + BODY,
     [("f( a , skip = frozenset ( )", 1, 8, 4)]),
    ("tuple default",
     'def f(a, names=("a", "b"),\n      strict=False):\n' + BODY,
     [('f( a , names = ( "a" , "b" )', 1, 8, 4)]),
    ("call defaults with keywords",
     "def f(a, at=D(2025, 6, 26), wait=timedelta(seconds=5),\n      strict=False):\n" + BODY,
     [("f( a , at = D ( 2025 , 6 , 26 )", 1, 8, 4)]),
    # outer keeps only its own `if` (ccn 2); stock lizard charged inner's three
    # conditions to it and read it 5.
    ("nested def",
     "def outer(a):\n    def inner(a, b=(),\n              c=1):\n" + _indented(BODY)
     + "    if a:\n        return inner(a)\n    return 0\n",
     [("outer.inner( a , b = ( )", 2, 9, 4), ("outer( a )", 1, 12, 2)]),
    ("async def",
     "async def fetch(a, headers=dict(),\n                retries=3):\n" + BODY,
     [("fetch( a , headers = dict ( )", 1, 8, 4)]),
    ("decorated def",
     "@cache\ndef f(a, b=(),\n      c=1):\n" + BODY,
     [("f( a , b = ( )", 2, 9, 4)]),
    ("method in a class",
     "class A:\n    def m(self, a=(),\n          c=1):\n" + _indented(BODY),
     [("m( self , a = ( )", 2, 9, 4)]),
    ("annotation dict[str, list[int]] opened across lines",
     "def f(a) -> dict[\n    str, list[int]\n]:\n" + BODY,
     [("f( a )", 1, 9, 4)]),
    # pyannote.metrics writes `) \` and the annotation on the next line: the
    # continuation is one token between the parameter list and the `->`.
    ("return annotation after a backslash continuation",
     "class M:\n    def __call__(self, a) \\\n            -> dict[str,\n                    int]:\n"
     + _indented(BODY),
     [("__call__( self , a )", 2, 10, 4)]),
    # A stub on the colon line: lizard ended it at line 5, before the `]: ...`.
    ("annotation opened, body on the colon line",
     "class P:\n    def getmesh(\n        self, image: Image\n    ) -> list[\n        tuple[int, int]\n"
     "    ]: ...\n\n\ndef after(a):\n    return a\n",
     [("getmesh( self , image : Image )", 2, 6, 1), ("after( a )", 9, 10, 1)]),
]


@pytest.mark.parametrize("name, source, expected", CUT_OFF, ids=[c[0] for c in CUT_OFF])
def test_a_signature_lizard_cut_off_reads_its_whole_span_and_real_ccn(name, source, expected):
    read = _read(source)
    assert read == expected
    assert sorted((start, end) for _, start, end, _ in read) == _ast_spans(source)


# --- slice 3: what lizard already read whole reads exactly as before ----------------

# (long_name, start, end, ccn, nloc, params), measured with stock lizard 1.24.0.
WHOLE = [
    ("signature on one line", "def g(a, b):\n" + BODY,
     [("g( a , b )", 1, 7, 4, 7, 2)]),
    # lizard lists no one-line def at all, and neither does the corrected reader.
    ("one-line def", "def g(x): return x\n", []),
    ("exploded parameters with a trailing comma",
     'def g(\n    a: int,\n    b: str = "",\n) -> bool:\n' + BODY,
     [('g( a : int , b : str = "" , )', 1, 10, 4, 10, 2)]),
    ("bracket-typed parameters",
     "def g(a: list[int], b: dict[str, list[int]]) -> int:\n" + BODY,
     [("g( a : list [ int ] , b : dict [ str , list [ int ] ] )", 1, 7, 4, 7, 2)]),
    ("docstring under an annotated def",
     'def g(a) -> int:\n    """Doc.\n\n    More.\n    """\n' + BODY,
     [("g( a )", 1, 11, 4, 7, 1)]),
    ("docstring under an unannotated def",
     'def g(a):\n    """Doc.\n\n    More.\n    """\n' + BODY,
     [("g( a )", 1, 11, 4, 7, 1)]),
    ("lambda defaults",
     "def g(a, key=lambda item: item[0], pick=lambda x, y: (x, y)):\n" + BODY,
     [("g( a , key = lambda item : item [ 0 ] , pick = lambda x , y : ( x , y )", 1, 7, 4, 7, 5)]),
    ("star args and kwargs", "def g(*a, **kwargs):\n" + BODY,
     [("g( * a , ** kwargs )", 1, 7, 4, 7, 2)]),
    ("empty tuple default with no break after it",
     "def g(a, bases=(), slots=False):\n" + BODY,
     [("g( a , bases = ( )", 1, 7, 4, 7, 2)]),
    ("line broken before the empty tuple default",
     "def g(a,\n      bases=(), slots=False):\n" + BODY,
     [("g( a , bases = ( )", 1, 8, 4, 8, 2)]),
    ("exploded parameters with a call default",
     "def g(\n    a=frozenset(),\n    b=1,\n):\n" + BODY,
     [("g( a = frozenset ( )", 1, 10, 4, 10, 1)]),
    # A stub on the colon line is listed only when a signature line after the
    # first ')' pushed a nesting level, as lizard lists it.
    ("exploded parameters with a parenthesized annotation, body on the colon line",
     "class R:\n    def __init__(\n        self,\n        expected: (\n            type[E] | tuple[type[E], ...]\n"
     "        ),\n        /,\n        *,\n        match: str | None = ...,\n    ) -> None: ...\n\n"
     "    def other(self):\n        return 1\n",
     [("__init__( self , expected : ( type [ E ] | tuple [ type [ E ] , ... ] )", 2, 10, 1, 9, 2),
      ("other( self )", 12, 13, 1, 2, 1)]),
    ("exploded parameters, body on the colon line",
     "class S:\n    def __init__(\n        self,\n        match: str | None,\n    ) -> None: ...\n\n"
     "    def other(self):\n        return 1\n",
     [("other( self )", 7, 8, 1, 2, 1)]),
]


@pytest.mark.parametrize("name, source, expected", WHOLE, ids=[w[0] for w in WHOLE])
def test_a_def_lizard_read_whole_keeps_its_reading(name, source, expected):
    records = analyze_source("shape.py", source)
    assert [(r.long_name, r.start, r.end, r.ccn, r.nloc, r.params) for r in records] == expected


def test_the_cognitive_pass_still_reads_the_file_as_python():
    """lizardcognitive keys on a reader name starting with "Python": a flat
    function of 4 conditions nested 2 deep scores 4 and nests 2, as under stock."""
    (record,) = analyze_source("shape.py", "def g(a, b):\n" + BODY)
    assert (record.cognitive, record.nesting) == (4, 2)


# --- slice 4: a def no reader finished is named and scored as nothing ---------------
#
# Scoring it would give ccn 1 whatever its body holds; ending the run over one
# file is what 0.7.1 stopped doing for an unreadable file. It takes the same road.


CUT_AT_END_OF_FILE = "def outer(a):\n    def inner(a, b=(),\n"


def test_a_def_cut_off_mid_signature_at_end_of_file_is_named_and_scores_nothing(capsys):
    records = analyze_source("shape.py", CUT_AT_END_OF_FILE)
    assert isinstance(records, UnanalyzableFile) and records == []
    assert "shape.py:2 outer.inner( a , b = ( )" in records.reason
    assert "shape.py:2 outer.inner( a , b = ( )" in capsys.readouterr().err


def test_the_batch_entry_point_scores_the_other_files_and_names_the_unfinished_one(tmp_path, capsys):
    (tmp_path / "ok.py").write_text("def g(a, bases=(),\n      slots=False):\n" + BODY, encoding="utf-8")
    (tmp_path / "bad.py").write_text(CUT_AT_END_OF_FILE, encoding="utf-8")
    records, _, _ = analyze_files(tmp_path, ["ok.py", "bad.py"], cache={})
    assert [(r.long_name, r.ccn) for r in records["ok.py"]] == [("g( a , bases = ( )", 4)]
    assert isinstance(records["bad.py"], UnanalyzableFile) and records["bad.py"] == []
    assert "bad.py:2 outer.inner( a , b = ( )" in capsys.readouterr().err


# --- registration --------------------------------------------------------------------


def test_register_makes_lizard_resolve_py_to_the_corrected_reader():
    register()
    assert lizard_languages.PythonReader is PythonSignatureReader
    assert lizard.get_reader_for("module.py") is PythonSignatureReader


def test_register_runs_twice_with_the_same_result():
    register()
    register()
    assert lizard.get_reader_for("module.py") is PythonSignatureReader


def test_register_raises_when_lizard_resolves_something_else(monkeypatch):
    monkeypatch.setattr(lizard, "get_reader_for", lambda _name: StockPythonReader)
    with pytest.raises(RuntimeError, match="did not take"):
        register()


def test_other_languages_keep_their_readers():
    register()
    assert lizard.get_reader_for("module.js").__name__ == "JavaScriptReader"
    assert lizard.get_reader_for("module.go").__name__ == "GoReader"
