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
])
def test_the_mask_blanks_what_a_regex_in_a_template_expression_holds(code, masked):
    assert lizardtypescript.mask_templates(code) == masked
