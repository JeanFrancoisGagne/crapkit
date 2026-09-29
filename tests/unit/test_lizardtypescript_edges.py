"""The TypeScript reader's template mask and its one refusal, read exactly.

Inside `${...}` a `/` after an operator or an opening bracket starts a regex
literal (ECMA-262 11.8.5, the goal symbol InputElementRegExp), so a `{` inside
that regex opens nothing; a `/` after a value divides. A function holding either
keeps its span and its one `if`: a `{` read as code would open a block the
template never closes.
"""
import pytest

from crapkit import lizardtypescript
from crapkit.analyze import analyze_source


def rows(path: str, code: str) -> list[tuple]:
    return [(r.long_name, r.start, r.end, r.ccn_std) for r in analyze_source(path, code)]


@pytest.mark.parametrize("expression", [
    "(/a{/).source",       # a regex after `(`, holding a `{`
    "( /a{/).source",      # the same after a space
    "s.length /2 }}",      # a division, then text that happens to hold braces
])
def test_a_template_s_slash_is_read_as_regex_or_division_by_what_precedes_it(expression):
    code = ("function f(s: string) {\n  const t = `${ " + expression + " }`;\n"
            "  if (s) { return t; }\n  return s;\n}\n")

    assert rows("src/f.ts", code) == [("f ( s )", 1, 5, 2)]


def test_an_arrow_body_with_a_less_than_before_a_comma_is_refused_by_name(capsys):
    assert rows("src/x.ts", "export const pair = [(a: number) => a < 1, 2];\n") == []

    assert capsys.readouterr().err.splitlines()[-1] == (
        "crapkit:   lizard failed on src/x.ts: src/x.ts:1: expression-arrow body has '<' before "
        "a comma; lizard cannot distinguish type arguments from an expression separator here; "
        "wrap that arrow body in parentheses or a block")


@pytest.mark.parametrize("code, masked", [
    # a regex after `(`: its backtick is blanked, as the mask's docstring says
    ('const t = `${ s.replace(/`/g, "") }`;\n', 'const t = `${ s.replace(/ /g, "") }`;\n'),
    # after `(` and a space: its brace is blanked
    ("const t = `${ ( /a{/).source }`;\n", "const t = `${ ( /a /).source }`;\n"),
    # after a value the slash divides, and the braces after it are the template's own
    ("const t = `${ n /2 }{`;\n", "const t = `${ n /2 }{`;\n"),
    # a division right before `{`: that brace is code, so the nested template after it is found
    ("const t = `${ x /{a:1}.a + `n` }`;\n", "const t = `${ x /{a:1}.a +  n  }`;\n"),
    # two divisions: the text between them is no regex, and its braces stay
    ("const t = `${ a / {b: 1}.b / c }`;\n", "const t = `${ a / {b: 1}.b / c }`;\n"),
])
def test_the_mask_blanks_what_a_regex_in_a_template_expression_holds(code, masked):
    assert lizardtypescript.mask_templates(code) == masked


@pytest.mark.parametrize("code, masked", [
    # a template at the file's first character is still a template
    ("`a${`b`}c`;\n", "`a${ b }c`;\n"),
    # a template the file never closes stays as lizard reads it, and the one before it keeps its mask
    ("const s = `a${`b`}c`;\nconst u = `oops\n", "const s = `a${ b }c`;\nconst u = `oops\n"),
])
def test_the_mask_reads_every_template_from_the_file_s_start_to_its_end(code, masked):
    assert lizardtypescript.mask_templates(code) == masked


G = "function g(p: number): number {\n  if (p) {\n    return 1;\n  }\n  return 0;\n}\n"


@pytest.mark.parametrize("path, code, expected", [
    # a curried arrow ended by `;`: the inner arrow, the outer one, and the function after both
    ("src/f.js", "const f = a => b => a + b;\n" + G.replace(": number", ""),
     [("(anonymous)", 1, 1, 1), ("f", 1, 1, 1), ("g ( p )", 2, 7, 2)]),
    # an arrow whose body is an arrow with a block body: both run to the block's `}`
    ("src/f.js", "run((s) => () => {\n  h(s);\n});\n",
     [("(anonymous)", 1, 3, 1), ("(anonymous)", 1, 3, 1)]),
    # a function expression after an array argument is anonymous
    ("src/f.js", "f([1], function () { return 2; });\n", [("(anonymous) ( )", 1, 1, 1)]),
    # `>` in an arrow body inside an array is a comparison, which closes no type argument
    ("src/f.ts", "export const pair = [(a: number) => a > 1, 2];\n", [("(anonymous)", 1, 1, 1)]),
    # the colon that closes a nested ternary leaves the arrow after it an arrow
    ("src/f.ts", "const v = a ? b ? c : d : (x: number) => x;\n", [("(anonymous)", 1, 1, 1)]),
    # a ternary before an arrow with a return type: each colon keeps its own meaning
    ("src/f.ts", "const v = a ? b : c;\nconst f = (x: number): { a: number } => ({ a: x });\n" + G,
     [("f ( x )", 2, 2, 1), ("g ( p )", 3, 8, 2)]),
])
def test_expression_arrows_are_read_one_function_each(path, code, expected):
    assert rows(path, code) == expected


def test_a_curried_arrow_over_lines_reads_both_functions():
    """The inner arrow's state ends before it has read a token of its own."""
    found = rows("src/f.js", "const f = (s) => () =>\n  h(s, {\n    a: 1,\n  });\n")

    assert (sorted(row[0] for row in found), found[-1]) == (["(anonymous)", "f ( s )"],
                                                          ("f ( s )", 1, 4, 1))
