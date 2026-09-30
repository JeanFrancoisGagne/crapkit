"""The readers' edges the weekly readers run left unchecked, each worked by hand
from the language's own reference.

Java (The Java Language Specification SE 21): a compact constructor (sec.
8.10.4) declares no method, in a top-level record and in a local one (sec.
14.3); `record` is a type word only before a record's name (sec. 3.9), so a
variable named record, `record instanceof T` and a pre-16 method whose return
type is named record declare no record; an annotation's name may be qualified
(sec. 9.7). register() names the reader lizard resolves when the rebind does
not take.
"""
import re

import pytest

import lizard
from crapkit import lizardjava, lizardswift
from crapkit.analyze import analyze_source

BODY = " {\n    if (n > 0) {\n      return n;\n    }\n    return 0;\n  }\n"
GUARD = "if (y < 0) throw new IllegalArgumentException();"


def _java(source: str) -> list[tuple]:
    return [(r.long_name, r.start, r.end, r.ccn_std) for r in analyze_source("A.java", source,
                                                                              note=False)]


# --- Java ---------------------------------------------------------------------------------------

def test_a_top_level_record_s_compact_constructor_is_no_method():
    """P { ... } checks the components and declares no method; lizard charges its
    `if` to nothing, so twice is the file's one row."""
    source = f"record P(int y) {{\n  P {{\n    {GUARD}\n  }}\n  int twice(int n){BODY}}}\n"

    assert _java(source) == [("P::twice( int n)", 5, 10, 2)]


def test_a_compact_constructor_after_a_method_leaves_the_next_method_its_row():
    source = (f"class O {{\n  record P(int y) {{\n  int twice(int n){BODY}  P {{\n    {GUARD}\n  }}\n"
              f"  int after(int n){BODY}}}\n}}\n")

    assert _java(source) == [("O::P::twice( int n)", 3, 8, 2), ("O::P::after( int n)", 12, 17, 2)]


def test_a_local_record_s_compact_constructor_is_no_method():
    """A record declared in go (sec. 14.3): its compact constructor's `if` is
    charged to go, as lizard charges a top-level one's to no method."""
    source = (f"class A {{\n  int go(int n) {{\n    record R(int y) {{\n      R {{\n        {GUARD}\n"
              f"      }}\n      int twice(int n){BODY}    }}\n    return new R(n).twice(n);\n  }}\n"
              f"  int after(int n){BODY}}}\n")

    assert _java(source) == [("A::go.R::twice( int n)", 7, 12, 2), ("A::go( int n)", 2, 15, 2),
                             ("A::after( int n)", 16, 21, 2)]


def test_record_instanceof_tests_a_variable_and_declares_no_local_record():
    """`record instanceof Integer` tests the parameter named record; read as a
    local record named instanceof, the real record R after it took the pending
    name, and R's compact constructor read as a method."""
    source = (f"class A {{\n  int go(Object record) {{\n    boolean b = record instanceof Integer;\n"
              f"    record R(int y) {{\n      R {{\n        {GUARD}\n      }}\n    }}\n"
              f"    return 0;\n  }}\n}}\n")

    assert _java(source) == [("A::go( Object record)", 2, 10, 2)]


def test_a_variable_named_record_before_a_brace_or_a_comma_opens_nothing():
    """`{1, record}` and `use(record); }` hold a variable: the `}` after it still
    closes its block, so go ends at line 7 and after keeps its row."""
    source = ("class A {\n  int go(int record) {\n    int[] a = {1, record};\n"
              "    Runnable r = () -> { use(record); };\n    if (record > 1) { return 1; }\n"
              f"    return 0;\n  }}\n  int after(int n){BODY}}}\n")

    assert _java(source) == [("A::go( int record)", 2, 7, 2), ("A::after( int n)", 8, 13, 2)]


def test_a_qualified_annotation_opens_no_method():
    """`@java.lang.Deprecated` and `@a.b.C(1)` are annotations with qualified
    names (sec. 9.7); `C(1)` is the annotation's arguments, not a method."""
    source = f"class A {{\n  @java.lang.Deprecated\n  int f(int n){BODY}  @a.b.C(1)\n  int g(int n){BODY}}}\n"

    assert _java(source) == [("A::f( int n)", 3, 8, 2), ("A::g( int n)", 10, 15, 2)]


def test_a_pre_16_method_returning_a_type_named_record_nests_nothing():
    """Before Java 16 `record` could name a type: `record make(int x);` declares a
    method, and the class after the interface is no member of a record `make`."""
    source = f"interface I {{\n  record make(int x);\n}}\nclass K {{\n  int k(int n){BODY}}}\n"

    assert _java(source) == [("K::k( int n)", 5, 10, 2)]


class _Stranger:
    """A reader class lizard could resolve a file to if its mechanism changed."""


@pytest.mark.parametrize("resolved, named", [
    (None, "no reader"),
    (_Stranger, f"{__name__}._Stranger"),
])
def test_a_java_rebind_that_does_not_take_names_what_lizard_resolves(monkeypatch, resolved, named):
    monkeypatch.setattr(lizard, "get_reader_for", lambda filename: resolved)

    with pytest.raises(RuntimeError) as refused:
        lizardjava.register()

    assert str(refused.value) == (
        f"crapkit.lizardjava.register() did not take: lizard resolves '.java' to {named}, not "
        f"crapkit.lizardjava.JavaReader. lizard {lizard.version} picks readers some other way "
        "than lizard_languages.languages(); rewrite register() against the new mechanism.")


# --- Swift --------------------------------------------------------------------------------------
#
# The Swift Programming Language (Swift 6): an accessor's word opens its block only
# right before it, an effect or, for a setter or an observer, the name it gives the
# value; a call to a method named get or set is no accessor; a comment is whitespace;
# `\( )` in a string holds an expression, parentheses and strings included.

def _swift(source: str) -> list[tuple]:
    return [(r.long_name, r.start, r.end, r.ccn_std) for r in analyze_source("a.swift", source,
                                                                              note=False)]


AFTER = "func h() {\n  if b { g() }\n}\n"


@pytest.mark.parametrize("call", ["cache.set(key) { print(1) }", "store.get { print(1) }",
                                  "get(key) { print(1) }"])
def test_a_call_to_a_method_named_get_or_set_with_a_trailing_closure_opens_no_accessor(call):
    source = f"func f() {{\n  {call}\n  if a {{ g() }}\n}}\n{AFTER}"

    assert _swift(source) == [("f", 1, 4, 2), ("h", 5, 7, 2)]


@pytest.mark.parametrize("between", [" // read it\n  ", " /* read */ ", " throws "])
def test_a_getter_reads_as_one_past_a_comment_or_an_effect(between):
    """`get // read it` then `{`, `get /* read */ {` and `get throws {` each open the
    getter's block: a comment is whitespace, and an effect may come between."""
    source = (f"var x: Int {{\n  get{between}{{\n    if a {{ return 1 }}\n    return 0\n  }}\n}}\n"
              f"{AFTER}")
    lines = source.count("\n")

    assert _swift(source) == [("get", 2, lines - 4, 2), ("h", lines - 2, lines, 2)]


def test_a_comma_inside_one_parameter_keeps_the_long_name_s_spacing():
    source = "func f(pair: (Int, Int), other: [Int: Int]) {\n  if pair.0 > 0 { g() }\n}\n"

    assert [(r.long_name, r.params) for r in analyze_source("a.swift", source, note=False)] == [
        ("f pair : Int , Int , other : [ Int : Int ]", 2)]


def test_an_initializer_used_in_a_default_keeps_its_spelling_in_the_long_name():
    source = "func f(x: Point = .init(), y: Int = 0) {\n  if a { g() }\n}\n"

    assert _swift(source) == [("f x : Point = . init , y : Int = 0", 1, 3, 2)]


@pytest.mark.parametrize("expression, ccn", [
    ("a && g(x)", 2),
    ('"x" + (a && b ? "y" : "z")', 3),
])
def test_an_interpolation_is_read_whole_from_its_first_character(expression, ccn):
    """Every decision in `\\( )` counts, from its first token: a call inside it or
    a string leading it changes nothing."""
    source = f'func f() {{\n  print("\\({expression})")\n}}\n'

    assert _swift(source) == [("f", 1, 3, ccn)]


def test_the_interpolation_pattern_holds_parentheses_to_its_depth_and_strings():
    """`_nested(2)` matches an interpolation's contents with groups two deep and a
    one-line string holding a `)`, and not a third group inside them."""
    body = f"(?:{lizardswift._nested(2)})*"

    assert re.fullmatch(body, 'f(g(x), "a)")') is not None
    assert re.fullmatch(body, "f(g(h(x)))") is None
    assert lizardswift._nested(0) == r'[^()"\\]|\\.|"(?:\\.|[^"\\\n])*"'


@pytest.mark.parametrize("end", ["\n", ""])
def test_a_file_that_ends_on_an_optional_type_is_read(end):
    source = f"func f() {{\n  if a {{ g() }}\n}}\nvar x: Int?{end}"

    assert _swift(source) == [("f", 1, 3, 2)]


def test_a_member_named_protocol_at_the_end_of_a_line_declares_no_protocol():
    """`conn.protocol` reads a member; the `func` on the next line is no protocol's name."""
    assert _swift(f"let p = conn.protocol\n{AFTER}") == [("h", 2, 4, 2)]


@pytest.mark.parametrize("resolved, named", [(None, "no reader"), (_Stranger, "_Stranger")])
def test_a_swift_rebind_that_does_not_take_names_what_lizard_resolves(monkeypatch, resolved, named):
    monkeypatch.setattr(lizard, "get_reader_for", lambda filename: resolved)

    with pytest.raises(RuntimeError) as refused:
        lizardswift.register()

    assert str(refused.value) == (
        f"crapkit.lizardswift.register() did not take: lizard resolves '.swift' to {named}, not "
        f"CorrectedSwiftReader. lizard {lizard.version} picks readers some other way than "
        "lizard_languages.languages(); rewrite register() against the new mechanism.")
