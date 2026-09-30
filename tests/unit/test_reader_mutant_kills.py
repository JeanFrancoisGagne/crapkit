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
import pytest

import lizard
from crapkit import lizardjava
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
    """A reader class lizard could resolve `.java` to if its mechanism changed."""


@pytest.mark.parametrize("resolved, named", [
    (None, "no reader"),
    (_Stranger, f"{__name__}._Stranger"),
])
def test_a_rebind_that_does_not_take_names_what_lizard_resolves(monkeypatch, resolved, named):
    monkeypatch.setattr(lizard, "get_reader_for", lambda filename: resolved)

    with pytest.raises(RuntimeError) as refused:
        lizardjava.register()

    assert str(refused.value) == (
        f"crapkit.lizardjava.register() did not take: lizard resolves '.java' to {named}, not "
        f"crapkit.lizardjava.JavaReader. lizard {lizard.version} picks readers some other way "
        "than lizard_languages.languages(); rewrite register() against the new mechanism.")
