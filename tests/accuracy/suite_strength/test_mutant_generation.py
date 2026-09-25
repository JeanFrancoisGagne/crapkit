"""Which mutants crapkit makes from a text, against lists worked out by hand.

This is the generation half of the `Mutation results` calc, called in-process
(crapkit.mutate.file_mutants, apply_mutant and mutation_language), so the
mutation killer suite, which leaves out every test that spawns a process, sees
it. The rules the expected lists come from:

- README.md:809: mutate flips comparisons, boundary shifts, boolean
  connectives and boolean literals. The operator table (crapkit/mutate.py,
  `_OPS`) maps `<` to `<=` and `>=`, `>` to `>=` and `<=`, `<=` to `<` and `>`.
- crapkit/mutate.py `_PROTECT`: a short operator that sits inside a longer one
  (`>` in `=>`, `<` in `..<`) is not that operator and grows no mutant. A short
  operator next to a longer one is still its own operator.
- Angle brackets in the C family, TypeScript, Rust, Java and Swift: a `<` ...
  `>` pair at one bracket depth is a type argument list when a type word or a
  type context (`:`, `as`, `function`, `new`, ...) comes right before it, and
  neither bracket is mutated. A `;`, `{` or `}` ends every pair still open. A
  pair that could be either (TypeScript's `f(a < b, c > d)`, the grammar
  ambiguity C# spec section 6.2.5 describes) is refused with a ToolError naming
  the line, never guessed. Text in strings and comments is not code.
- Go spec, Send statements and Receive operator: `<-` is the channel operator,
  not a comparison, and `ch <=- v` does not compile (SS5 in rulings.tsv).

Every expected list below was written from these rules before the test ran.
"""
from __future__ import annotations

import pytest

from accuracy.kit import rulings
from crapkit.errors import ToolError
from crapkit.mutate import Mutant, apply_mutant, file_mutants, mutation_language

AMBIGUOUS = "cannot distinguish type arguments from comparisons"

# (id, language, text, changed lines or None, [(line, mutated line, op)])
HAND = [
    ("semicolon-ends-an-open-angle", "typescript", "r = q ? x : a < b;\ns = c > d;\n", None,
     [(1, "r = q ? x : a <= b;", "< -> <="), (1, "r = q ? x : a >= b;", "< -> >="),
      (2, "s = c >= d;", "> -> >="), (2, "s = c <= d;", "> -> <=")]),
    ("close-brace-ends-an-open-angle", "typescript", "f({ k: t ? x : a < b }, [c > d]);\n", None,
     [(1, "f({ k: t ? x : a <= b }, [c > d]);", "< -> <="),
      (1, "f({ k: t ? x : a >= b }, [c > d]);", "< -> >="),
      (1, "f({ k: t ? x : a < b }, [c >= d]);", "> -> >="),
      (1, "f({ k: t ? x : a < b }, [c <= d]);", "> -> <=")]),
    ("open-brace-ends-an-open-angle", "typescript", "if (t ? x : a < b) { y = c > d; }\n", None,
     [(1, "if (t ? x : a <= b) { y = c > d; }", "< -> <="),
      (1, "if (t ? x : a >= b) { y = c > d; }", "< -> >="),
      (1, "if (t ? x : a < b) { y = c >= d; }", "> -> >="),
      (1, "if (t ? x : a < b) { y = c <= d; }", "> -> <=")]),
    ("comparison-after-a-type-argument-list", "typescript",
     "const q = x as Array<number> > y;\n", None,
     [(1, "const q = x as Array<number> >= y;", "> -> >="),
      (1, "const q = x as Array<number> <= y;", "> -> <=")]),
    ("parentheses-inside-type-arguments", "typescript",
     "let handlers: Array<(e: Event) => void> = [];\n", None, []),
    ("brackets-inside-type-arguments", "typescript", "let grid: Array<number[]> = [];\n", None, []),
    ("braces-inside-type-arguments", "typescript", "let rows: Array<{ k: number }> = [];\n", None, []),
    ("a-string-closes-no-angle", "typescript", "const r = f(a < b, 'x > y');\n", None,
     [(1, "const r = f(a <= b, 'x > y');", "< -> <="), (1, "const r = f(a >= b, 'x > y');", "< -> >=")]),
    ("builtin-type-word-before-the-angle", "typescript", "const xs = Array<Foo>();\n", None, []),
    ("type-context-two-words-back-at-the-start", "typescript",
     "function f<T>(x: T): T { return x; }\n", None, []),
    ("java-word-after-the-angle-at-the-end-of-text", "java", "Foo<Bar> baz", None, []),
    ("fold-expression-comparison-before-an-ellipsis", "cpp", "return (args<...);\n", None,
     [(1, "return (args<=...);", "< -> <="), (1, "return (args>=...);", "< -> >=")]),
    ("greater-inside-an-arrow-at-column-zero", "swift", "=> b\n", None, []),
    ("chained-comparison-after-a-longer-operator", "python", "ok = a<=b<c\n", None,
     [(1, "ok = a<b<c", "<= -> <"), (1, "ok = a>b<c", "<= -> >"),
      (1, "ok = a<=b<=c", "< -> <="), (1, "ok = a<=b>=c", "< -> >=")]),
]

# (id, language, text, changed lines, the line the refusal names)
REFUSED = [
    ("ambiguous-pair-on-its-first-line", "typescript", "const r = f(a < b,\n            c > d);\n", {1}, 1),
    ("ambiguous-pair-on-its-second-line", "typescript", "const r = f(a < b,\n            c > d);\n", {2}, 2),
    ("ambiguous-pair-after-a-call", "typescript", "x = f()<b>(c);\n", None, 1),
    ("java-angle-closing-the-text", "java", "x = Foo<Bar>", None, 1),
]


@pytest.mark.parametrize("language, text, changed, expected",
                         [pytest.param(*row[1:], id=row[0]) for row in HAND])
def test_each_text_grows_the_mutants_worked_by_hand(language, text, changed, expected):
    got = [(m.line, m.mutated, m.op) for m in file_mutants(text, changed, language)]
    assert got == expected


@pytest.mark.parametrize("language, text, changed, line",
                         [pytest.param(*row[1:], id=row[0]) for row in REFUSED])
def test_an_ambiguous_angle_pair_is_refused_on_its_line(language, text, changed, line):
    with pytest.raises(ToolError) as refused:
        file_mutants(text, changed, language)
    assert str(refused.value) == f"ambiguous {language} angle syntax on line {line}: {AMBIGUOUS}"


def test_a_mutant_carries_its_own_line_and_leaves_the_path_to_the_caller():
    text = "x = 1\nif a < b: pass"
    assert file_mutants(text, None, "python") == [
        Mutant("", 2, "if a < b: pass", "if a <= b: pass", "< -> <="),
        Mutant("", 2, "if a < b: pass", "if a >= b: pass", "< -> >=")]


@pytest.mark.parametrize("text, line, expected", [
    pytest.param("x = 1\nif a < b: pass", 2, "x = 1\nif a <= b: pass", id="last-line-without-newline"),
    pytest.param("if a < b:\n    x = 1", 1, "if a <= b:\n    x = 1", id="first-line-before-a-bare-last"),
])
def test_apply_replaces_one_line_and_keeps_the_texts_last_newline(text, line, expected):
    original = text.splitlines()[line - 1]
    mutant = Mutant("a.py", line, original, original.replace("<", "<="), "< -> <=")
    assert apply_mutant(text, mutant) == expected


@pytest.mark.parametrize("path, language", [
    ("notes.txt", "typescript"), ("Makefile", "typescript"), ("a.py", "python"), ("a.go", "go"),
])
def test_a_path_without_a_listed_suffix_takes_the_c_family_table(path, language):
    assert mutation_language(path) == language


@rulings.applies("SS5")
def test_ss5_the_go_channel_arrow_is_not_a_comparison():
    mutated = [m.mutated for m in file_mutants("ch <- v\nv := <-ch\n", None, "go")]
    rulings.pin_ruling("SS5", crapkit="|".join(mutated) or "none", oracle="none")
