"""The Rust reader around comments, `impl ... for` and signatures with no body.

The Rust Reference gives each case: a comment is whitespace to the parser
("Comments"), so `a /* c */ || b` still has `a` as the left operand of its
`||`; the `for` of `impl Trait for Type` ("Implementations") is no loop,
whatever the trait's name starts with; and a trait's required method or a
signature in a macro's input has no body, so the item after it and the block
around it read as they would without it.

Each row is (long name, start line, end line, ccn, cognitive, parameter count),
counted by hand.
"""
import pytest

from crapkit.analyze import analyze_source


def rows(code: str) -> list[tuple]:
    return [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive, r.params)
            for r in analyze_source("src/a.rs", code)]


@pytest.mark.parametrize("code", [
    "fn f(a: bool, b: bool) -> bool {\n    a // left:\n        || b\n}\n",
    "fn f(a: bool, b: bool) -> bool {\n    a /* left */ || b\n}\n",
])
def test_a_comment_before_an_operator_leaves_its_left_operand(code):
    """`||` is one decision: ccn 2 and cognitive 1."""
    [row] = rows(code)

    assert (row[0], row[3], row[4]) == ("f a : bool , b : bool", 2, 1)


def test_an_implementation_for_a_trait_named_with_an_underscore_is_no_loop():
    code = "fn outer() {\n    struct S;\n    trait _T {}\n    impl _T for S {}\n    if true {}\n}\n"

    assert rows(code) == [("outer", 1, 6, 2, 1, 0)]


def test_a_method_after_a_required_one_keeps_every_parameter():
    code = ("trait T {\n    fn a(&self);\n    fn b(&self, x: u8, y: u8) -> u8 {\n        if x > y {\n"
            "            x\n        } else {\n            y\n        }\n    }\n}\n")

    assert rows(code) == [("b & self , x : u8 , y : u8", 3, 9, 2, 2, 3)]


def test_a_signature_that_ends_a_macro_block_closes_that_block():
    code = "fn outer() {\n    m! { fn f() }\n    if true {}\n}\nfn g() {}\n"

    assert rows(code) == [("outer", 1, 4, 2, 1, 0), ("g", 5, 5, 1, 0, 0)]
