"""The shell reader's edges the weekly readers run left unchecked, each worked by
hand from the Bash Reference Manual and the reader's own docstring.

Arithmetic (sec. 6.5): inside `(( ))` the `;;` of `for ((;;))` writes a loop's
empty clauses and ends no case arm, even inside an arm, and a `?` in a command
substituted inside arithmetic is a glob again. Case (sec. 3.2.5.2): `;&` and
`;;&` end an arm, so the next word is a pattern, `done)` included; a case nested
in an arm closes before the outer arm's `;;`. Redirections (sec. 3.6): the word
after `>&` or `<&` names a file, never a reserved word. Quoting (sec. 3.1.2): a
case and a backquoted command inside a double-quoted string close before the
string does, so a heredoc after the string opens. Each script passes `bash -n`.
"""
import re

import pytest

from crapkit import lizardshell
from crapkit.analyze import analyze_source


def _rows(source: str) -> list[tuple]:
    """(name, start, end, ccn, cognitive, nesting) of each function."""
    return [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive, r.nesting)
            for r in analyze_source("a.sh", source, note=False)]


IF_BODY = "if a; then b; fi\n"


@pytest.mark.parametrize("pattern", ["a)", "(a)"])
def test_the_semicolons_of_an_arithmetic_for_inside_a_case_arm_end_no_arm(pattern):
    """ccn 1 + the arm's `;;` + for; cognitive case 1 + for 2."""
    source = f"f() {{\n  case $x in\n    {pattern} for ((;;)); do break; done ;;\n  esac\n}}\n"

    assert _rows(source) == [("f()", 1, 5, 3, 3, 2)]


@pytest.mark.parametrize("before", ["", "  case $x in\n    a) b ;;\n  esac\n"])
def test_an_array_after_an_arithmetic_for_holds_words(before):
    """The `if` inside `arr=( )` is a word, after a closed case or none: ccn counts
    the for and the arm only."""
    source = (f"f() {{\n{before}  for ((;;)); do break; done\n  arr=(\n    if\n  )\n}}\n")
    arms = before.count(";;")

    assert _rows(source) == [("f()", 1, 6 + 3 * arms, 2 + arms, 1 + arms, 1)]


def test_a_nested_case_closes_before_the_outer_arm_s_semicolons():
    """Three `;;` arms: ccn 4; cognitive case 1 + inner case 2."""
    source = ("f() {\n  case $x in\n    a)\n      case $y in\n        b) echo b ;;\n      esac\n"
              "      ;;\n    c) echo c ;;\n  esac\n}\n")

    assert _rows(source) == [("f()", 1, 10, 4, 3, 2)]


def test_a_glob_in_a_command_substituted_inside_arithmetic_is_no_ternary():
    assert _rows("f() {\n  echo $(( $(ls a?b | wc -l) + 1 ))\n}\n") == [("f()", 1, 3, 1, 0, 0)]


@pytest.mark.parametrize("redirect", [">&", "<&"])
def test_the_word_after_a_descriptor_redirection_names_a_file(redirect):
    """`fi` after `>&` or `<&` is a file name: the inner if still nests at 2."""
    source = (f"f() {{\n  if a; then\n    echo x {redirect} fi\n    if b; then c; fi\n  fi\n}}\n")

    assert _rows(source) == [("f()", 1, 6, 3, 3, 2)]


def test_after_a_fallthrough_arm_a_word_named_done_is_a_pattern():
    """`;&` ends the arm, so `done)` is a pattern and the if in its arm nests under
    while and case: ccn 1 + while + the `;;` arm + if; cognitive 1 + 2 + 3."""
    source = ("f() {\n  while a; do\n  case $x in\n    a) echo a ;&\n"
              "    done) if b; then c; fi ;;\n  esac\n  done\n}\n")

    assert _rows(source) == [("f()", 1, 8, 4, 6, 3)]


def test_a_subshell_body_ends_at_its_paren_after_a_for_in():
    """The `in` of a for opens no case, so the body's `)` still closes f."""
    source = f'f() (\n  for x in a b; do\n    echo "$x"\n  done\n)\ng() {{\n  {IF_BODY}}}\n'

    assert _rows(source) == [("f()", 1, 5, 2, 1, 1), ("g()", 6, 8, 2, 1, 1)]


@pytest.mark.parametrize("line", [
    "x=\"`echo 'a\"b'`\"; cat <<EOF",
    'x="`date`" <<EOF',
    'x="$(case $os in Linux) echo l;; esac)"; cat <<EOF',
    'x="$( (cd a; ls) )" <<EOF',
])
def test_a_heredoc_after_a_string_holding_a_substitution_opens(line):
    """The string closes before `<<`, so the body is blanked: only the case arm of
    the third line counts."""
    source = f"f() {{\n  {line}\n{IF_BODY}EOF\n}}\n"
    arm = int("case" in line)

    assert _rows(source) == [("f()", 1, 5, 1 + arm, arm, arm)]


@pytest.mark.parametrize("text", ["\n$(date)\n", "$(date)\nmore\n"])
def test_the_newlines_around_a_substitution_in_a_string_keep_their_lines(text):
    source = f'f() {{\n  x="{text}"\n  {IF_BODY}}}\ng() {{\n  {IF_BODY}}}\n'

    assert _rows(source) == [("f()", 1, 6, 2, 1, 1), ("g()", 7, 9, 2, 1, 1)]


def test_the_string_rule_s_parens_nest_to_the_depth_asked_and_hold_a_case():
    """_parens(2) matches what sits in a `( )` holding one more pair and no third;
    _parens(1) holds a case whose pattern ends in `)`; _loop gives back nothing."""
    two = r"\(" + lizardshell._parens(2) + r"\)"

    assert re.fullmatch(two, "(a (b) c)") is not None
    assert re.fullmatch(two, "(a (b (c)) d)") is None
    assert re.fullmatch(r"\$\(" + lizardshell._parens(1) + r"\)",
                        "$(case $os in Linux) echo l;; esac)") is not None
    assert lizardshell._loop("a|b") == "(?>(?:a|b)*)"
