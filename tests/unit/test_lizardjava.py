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

    assert _rows(source) == [("value( int n)", 4, 9, 2)]


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
        ("Outer::apply( int a , int b)", 4, 1), ("Outer::apply( int a , int b)", 7, 2),
        ("Outer::Op( String s)", 11, 1), ("Outer::twice( int a)", 12, 1),
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
        ("get( int n)", 3, 8, 2)),
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

    assert _rows(source) == [("check( int n)", 5, 10, 2)]


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
