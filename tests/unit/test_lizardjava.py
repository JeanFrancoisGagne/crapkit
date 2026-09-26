"""The Java reader: every method has a row under its own name, and nothing else has one.

lizard's Java states hid methods after an annotated local variable, after an
annotation element's `default {}` and inside an enum constant's body or a
field's anonymous class, and named a row after an annotation or an enum
constant. The hand values come from The Java Language Specification SE 21:
sec. 8.4 (methods), 8.9.1 (enum constants), 9.6.1 (annotation elements),
9.7 (annotations) and 15.9.5 (anonymous classes).
"""
import pytest

import lizard
from crapkit import lizardjava
from crapkit.analyze import analyze_source

BODY = " {\n        if (n > 0) {\n            return n;\n        }\n        return 0;\n    }\n"


def _rows(source: str) -> list[tuple]:
    return [(r.long_name, r.start, r.end, r.ccn_std) for r in analyze_source("A.java", source,
                                                                              note=False)]


def test_an_enum_constant_body_holds_methods_and_is_no_method():
    """sec. 8.9.1: `ONE() { ... }` is a constant whose body declares value."""
    source = ("enum C {\n    ONE() {\n        @Override\n        int value(int n)" + BODY
              + "    };\n\n    abstract int value(int n);\n}\n")

    assert _rows(source) == [("C::value( int n)", 4, 9, 2)]


def test_every_constant_of_an_enum_is_read_whatever_it_holds():
    """Annotations, arguments and bodies on the constants, a constructor and a
    method after them, and a method after the enum."""
    source = ("class Outer {\n  enum Op {\n    @Deprecated PLUS(\"+\") {\n"
              "      int apply(int a, int b) { return a + b; }\n    },\n"
              "    MINUS(f(\"-\")) {\n      int apply(int a, int b) { return a > b ? a - b : b - a; }\n"
              "    },\n    NONE;\n    private final String sym;\n    Op(String s) { sym = s; }\n"
              "    int twice(int a) { return apply(a, a); }\n  }\n"
              "  int after() { return 1; }\n}\n")

    assert [(name, start, ccn) for name, start, _, ccn in _rows(source)] == [
        ("Outer::Op::apply( int a , int b)", 4, 1), ("Outer::Op::apply( int a , int b)", 7, 2),
        ("Outer::Op::Op( String s)", 11, 1), ("Outer::Op::twice( int a)", 12, 1),
        ("Outer::after()", 14, 1)]


def test_an_enum_with_constants_only_ends_where_its_brace_does():
    source = "enum E { A, B }\nclass K {\n    int k(int n)" + BODY + "}\n"

    assert _rows(source) == [("K::k( int n)", 3, 8, 2)]


ANNOTATED = {  # label: (source, the one row, worked by hand)
    "argument annotation after a bare one": (
        "class B {\n    @Deprecated\n    @InlineMe(replacement = \"B.parse(json)\", imports = \"B\")\n"
        "    public static int inlined(int n)" + BODY + "}\n", ("B::inlined( int n)", 4, 9, 2)),
    "two bare annotations on one line": (
        "class B {\n    @Override @Nullable public Object get(int n)" + BODY + "}\n",
        ("B::get( int n)", 2, 7, 2)),
    "annotated record": (
        "@Deprecated\nrecord P(int x) {\n    int get(int n)" + BODY + "}\n",
        ("P::get( int n)", 3, 8, 2)),
}


@pytest.mark.parametrize("label", ANNOTATED)
def test_an_annotation_leaves_the_declaration_after_it_whole(label):
    """sec. 9.7: annotations are modifiers. lizard dropped the token after a bare
    annotation, so a second annotation with arguments read as the method, and
    `@Deprecated record P(int x)` as a method named P that hid get."""
    source, row = ANNOTATED[label]

    assert _rows(source) == [row]


def test_an_annotated_local_variable_hides_no_method_after_it():
    """lizard read the annotation's arguments with the counter the method body
    kept its braces in, never saw the `)` close them, and read the rest of the
    file as arguments."""
    source = ("class A {\n    int first(Object o) {\n        @SuppressWarnings(\"unchecked\")\n"
              "        int x = (int) o;\n        return x;\n    }\n\n    int second(int n)" + BODY
              + "}\n")

    assert _rows(source) == [("A::first( Object o)", 2, 6, 1), ("A::second( int n)", 8, 13, 2)]


def test_an_anonymous_class_in_an_interface_field_holds_methods_and_is_no_method():
    """sec. 15.9.5: `new FieldAnon() { ... }` declares check; lizard listed a
    method named FieldAnon and hid check."""
    source = ("interface FieldAnon {\n    FieldAnon FILTER =\n        new FieldAnon() {\n"
              "            @Override\n            public int check(int n)" + BODY
              + "        };\n\n    int check(int n);\n}\n")

    assert _rows(source) == [("FieldAnon::check( int n)", 5, 10, 2)]


ELEMENT_DEFAULTS = ["{}", "{\"a\", \"b\"}", "\"x\"", "@Other(v = {1})"]


@pytest.mark.parametrize("default", ELEMENT_DEFAULTS)
def test_an_annotation_element_default_is_no_body(default):
    """sec. 9.6.1: `String[] alternate() default {};` declares an element with no
    body. lizard read a braced default as one, and any other default as the
    start of a parameter declaration that ran to the next `{` in the file."""
    source = (f"@interface Element {{\n    String value();\n    String[] alternate() default {default};\n"
              "}\nclass K {\n    int k(int n)" + BODY + "}\n")

    assert _rows(source) == [("K::k( int n)", 6, 11, 2)]


PARAMETERS = [  # (label, parameter list, hand count): sec. 8.4.1
    ("array after the name", "String args[]", 1),
    ("array after the type", "int[] a, int b", 2),
    ("annotated and final", "final @Nullable String s", 1),
    ("generic and variadic", "Map<String, List<Integer>> m, int... rest", 2),
    ("none", "", 0),
]


@pytest.mark.parametrize("params,hand", [case[1:] for case in PARAMETERS],
                         ids=[case[0] for case in PARAMETERS])
def test_params_counts_every_declared_parameter(params, hand):
    """lizard names a parameter after the last word of its declaration, so
    `String args[]` counted none."""
    (record,) = analyze_source("A.java", f"class A {{\n    void f({params}) {{\n    }}\n}}\n",
                               note=False)

    assert record.params == hand


NESTED = {  # label: (source, the nested method's long name)
    "anonymous class": ("class A {\n    void go() {\n        Runnable r = new Runnable() {\n"
                        "            public void run() { work(); }\n        };\n    }\n}\n",
                        "A::go.run()"),
    "local class": ("class A {\n    int outer(int a) {\n        class L {\n"
                    "            int f(int x) { return x; }\n        }\n"
                    "        return new L().f(a);\n    }\n}\n", "A::outer.L::f( int x)"),
    "anonymous class in an anonymous class": (
        "class A {\n    void go() {\n        new Thread(new Runnable() {\n"
        "            public void run() {\n                later(new Runnable() {\n"
        "                    public void run() { work(); }\n                });\n"
        "            }\n        });\n    }\n}\n", "A::go.run.run()"),
}


@pytest.mark.parametrize("label", NESTED)
def test_a_method_nested_in_a_method_is_named_once_after_its_class(label):
    """lizard qualified the name with every level open around it, and the
    enclosing method's own name already held its class: `A::A::go.run`."""
    source, name = NESTED[label]

    names = [r.long_name for r in analyze_source("A.java", source, note=False)]

    assert names[0] == name


@pytest.mark.parametrize("head", ["class A {", "interface A {", "class A { static {}"])
def test_a_record_declared_first_in_a_body_is_no_method(head):
    """sec. 8.10: lizard took the `{` before `record` for the start of a name,
    so a record declared first in a class or interface body read as a method
    named after the record, and the record's methods had no row."""
    source = head + "\n    record S(int y) {\n        int h(int n)" + BODY + "    }\n}\n"

    rows = [(r.start, r.end, r.ccn_std) for r in analyze_source("A.java", source, note=False)]

    assert rows[-1] == (3, 8, 2)


TYPE_NAMES = {  # label: (source, the method's long name)
    "enum": ("class A {\n    enum F {\n        Z;\n        int g() { return 3; }\n    }\n}\n",
             "A::F::g()"),
    "interface": ("class A {\n    interface J {\n        default int f() { return 1; }\n    }\n}\n",
                  "A::J::f()"),
    "record": ("class A {\n    record S(int y) implements Comparable<S> {\n"
               "        int h() { return y; }\n    }\n}\n", "A::S::h()"),
    "generic record with an annotated component": (
        "class A {\n    record P<T>(@Size(max = {1}) T a) {\n        int h() { return 1; }\n"
        "    }\n}\n", "A::P::h()"),
    "top-level enum": ("enum E {\n    X;\n    int d() { return 2; }\n}\n", "E::d()"),
    "top-level interface": ("interface I {\n    static int s() { return 2; }\n}\n", "I::s()"),
    "enum in a method": ("class A {\n    void f() {\n        enum E { X; int d() { return 2; } }\n"
                         "    }\n}\n", "A::f.E::d()"),
    "anonymous class in a nested class": (
        "class A {\n    class G {\n        void e() {\n"
        "            Runnable r = new Runnable() { public void run() { } };\n        }\n    }\n}\n",
        "A::G::e.run()"),
    "sealed class": ("sealed class Shape permits Circle, Square {\n    int area() { return 0; }\n}\n",
                     "Shape::area()"),
    "anonymous class after a nested class": (
        "class A {\n    class B {\n    }\n    void go() {\n"
        "        Runnable r = new Runnable() { public void run() { } };\n    }\n}\n",
        "A::go.run()"),
}


@pytest.mark.parametrize("label", TYPE_NAMES)
def test_a_method_is_named_after_every_type_around_it(label):
    """sec. 8.9, 8.10 and 9.1: an enum, a record and an interface name their
    methods as a class does. lizard named a method after classes only, so the
    methods of two enums in one class shared one name, told apart only by an
    ordinal. A method of an anonymous class took the name of the last class
    declared before its method, `B::go.run`, and one in a nested class lost
    the outer class, `G::e.run`."""
    source, name = TYPE_NAMES[label]

    names = [r.long_name for r in analyze_source("A.java", source, note=False)]

    assert names[0] == name


def test_a_method_or_a_field_named_record_opens_no_type():
    source = ("class A {\n    Record record;\n    void record(int x) { }\n"
              "    int after() { return 1; }\n}\n")

    names = [r.long_name for r in analyze_source("A.java", source, note=False)]

    assert names == ["A::record( int x)", "A::after()"]


LOCAL_TYPES = {  # label: the head of a type declared in outer's body (sec. 14.3)
    "class": "class L {",
    "enum": "enum L { ONE;",
    "record": "record L(int x) {",
    "generic record after an annotation": "@Deprecated record L<T>(T x) {",
    "interface": "interface L {",
}


@pytest.mark.parametrize("label", LOCAL_TYPES)
def test_a_type_declared_in_a_method_holds_methods_the_method_does_not_pay_for(label):
    """sec. 14.3: a method body may declare a class, enum, record or interface.
    lizard read a local record or interface as statements of the method around
    it, which took the ccn of their methods and left them no row."""
    modifier = "default " if label == "interface" else ""
    source = ("class A {\n    int outer(int a) {\n        " + LOCAL_TYPES[label] + "\n"
              "            " + modifier + "int twice(int n) { if (n > 0) { return n; } return 0; }\n"
              "        }\n        return a;\n    }\n}\n")

    rows = [(r.start, r.end, r.ccn_std) for r in analyze_source("A.java", source, note=False)]

    assert rows == [(4, 4, 2), (2, 7, 1)]


TRAILING_MEMBERS = {  # label: a type in outer's body that ends on a member with no body
    "field after a method in a local class": "class L { int f() { return 1; } int y; }",
    "field in an anonymous class": "Runnable r = new Runnable() { public void run() { } int y = 3; };",
    "abstract method in a local interface": "interface L { int f(int x); }",
    "abstract method in a local abstract class": "abstract class L { abstract int f(); }",
    "field in a local record": "record L(int x) { static int y; }",
}


@pytest.mark.parametrize("label", TRAILING_MEMBERS)
def test_a_member_with_no_body_leaves_the_method_around_its_class_its_row(label):
    """A field or an abstract method is no function, so the method the class
    sits in stays current past it. lizard kept the member's name current, and
    at the method's closing brace the row went out under that name, from the
    member's line."""
    source = ("class A {\n    int outer(int a) {\n        " + TRAILING_MEMBERS[label] + "\n"
              "        if (a > 0) {\n            return a;\n        }\n        return 0;\n    }\n}\n")

    rows = analyze_source("A.java", source, note=False)

    assert [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive) for r in rows][-1] == (
        "A::outer( int a)", 2, 8, 2, 1)


def test_a_variable_named_record_starts_no_record():
    """`record` is a keyword only where a record's name follows it (sec. 3.9)."""
    source = ("class A {\n    int outer(Object record) {\n        if (record instanceof String s) {\n"
              "            return s.length();\n        }\n        record = null;\n"
              "        return record == null ? 0 : 1;\n    }\n\n    int after(int n)" + BODY + "}\n")

    rows = [(r.long_name, r.start, r.end, r.ccn_std) for r in analyze_source("A.java", source,
                                                                              note=False)]

    assert rows == [("A::outer( Object record)", 2, 8, 3), ("A::after( int n)", 10, 15, 2)]


def test_java_resolves_to_crapkits_reader():
    assert lizard.get_reader_for("A.java") is lizardjava.JavaReader


def test_register_twice_changes_nothing():
    lizardjava.register()
    lizardjava.register()

    assert lizard.get_reader_for("A.java") is lizardjava.JavaReader


def test_register_names_both_classes_when_the_rebind_does_not_take(monkeypatch):
    monkeypatch.setattr(lizard, "get_reader_for", lambda _: None)

    with pytest.raises(RuntimeError, match="crapkit.lizardjava.JavaReader"):
        lizardjava.register()
