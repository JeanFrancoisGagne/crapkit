"""The cognitive increments a token's neighbours decide, each counted by hand.

G. Ann Campbell, Cognitive Complexity v1.7 (SonarSource, 2023): `if`, a loop, a
`switch` and a `do while` pay +1 and the nesting they sit in (B1, B3); only those
structures nest, so a bare block adds no level (B2); a `switch`'s cases are free;
a `break` or `continue` pays only when it names a label (B1); and the
null-coalescing shorthands pay nothing ("Ignore shorthand"). A Python comment or
whitespace-only line is no logical line and leaves the indentation where it was
(The Python Language Reference, 2.1.7 "Blank lines" and 2.1.8 "Indentation").
"""
import pytest

from crapkit.analyze import analyze_source


def cognitive(path: str, code: str) -> list[int]:
    return [row.cognitive for row in analyze_source(path, code)]


@pytest.mark.parametrize("path, code, score", [
    # the `while` that closes a do-while is the loop the `do` already paid for
    ("a.c", "void f(int a) {\n  do {\n    a--;\n  } while (a);\n}\n", 1),
    # a bare block inside an `if` opens no level: the inner `if` pays +2, not +3
    ("a.c", "void f(int a, int b) {\n  if (a) {\n    {\n      if (b) {\n        return;\n"
            "      }\n    }\n  }\n}\n", 3),
    # nor does one after an `else`: the `if`, the `else` and a top-level `if`
    ("a.c", "void f(int a, int b) {\n  if (a) {\n    return;\n  } else {\n    a = 1;\n  }\n"
            "  {\n    if (b) {\n      return;\n    }\n  }\n}\n", 3),
    # Zig's `else =>` is the switch's default prong: the `if` and the switch
    ("a.zig", "fn f(x: u8, y: bool) u8 {\n    if (y) {\n        return 0;\n    }\n"
              "    return switch (x) {\n        0 => 1,\n        else => 2,\n    };\n}\n", 2),
])
def test_a_structure_pays_once_and_only_structures_nest(path, code, score):
    assert cognitive(path, code) == [score]


@pytest.mark.parametrize("path, code, score", [
    # a comment after a `break` names no label: the `for` +1 and the `if` +2
    ("a.js", "function f(a) {\n  for (;;) {\n    if (a) break // out\n  }\n}\n", 3),
    ("a.js", "function f(a) {\n  for (;;) {\n    if (a) break /* out */\n  }\n}\n", 3),
    # nor does the `)` a Rust `break` expression is an argument to: the `loop` +1
    ("a.rs", "fn f() {\n    loop {\n        g(break);\n    }\n}\n", 1),
    # nor the line after a Python `break`, which has no labels: the `for` +1
    ("a.py", "def f(xs):\n    for x in xs:\n        break\n    return 1\n", 1),
])
def test_a_jump_pays_only_for_a_label(path, code, score):
    assert cognitive(path, code) == [score]


@pytest.mark.parametrize("path, code, score", [
    # C#'s null-conditional `?.`: only the `??` run pays
    ("a.cs", "class C {\n  int F(C a) {\n    return a?.G() ?? 1;\n  }\n}\n", 1),
    # a nullable type in a cast decides nothing
    ("a.cs", "class C {\n  object F(object a) {\n    return (int?)a;\n  }\n}\n", 0),
    # GNU C's `?:` supplies a default the way `??` does, and has no second branch to read
    ("a.c", "int f(int a) {\n  return a ?: 1;\n}\n", 0),
])
def test_a_question_mark_that_is_no_conditional_pays_nothing(path, code, score):
    assert cognitive(path, code) == [score]


@pytest.mark.parametrize("code", [
    # a comment at column 0 inside the `if a:` block leaves `if b:` nested in it
    "def f(a, b):\n    if a:\n        x = 1\n# note\n        if b:\n            return x\n    return 0\n",
    # a whitespace-only first line is a blank line
    "  \ndef f(a, b):\n    if a:\n        if b:\n            return 1\n    return 0\n",
])
def test_a_line_python_ignores_moves_no_nesting(code):
    """`if a:` +1 and the `if b:` inside it +2."""
    assert cognitive("a.py", code) == [3]
