"""The PowerShell reader's edges the weekly readers run left unchecked, each worked
by hand from the reader's docstring and checked against Windows PowerShell 5.1's
Parser.ParseInput, which parses every script here without an error but the stray
closers, which the reader is documented to survive.

A word that starts with a letter reads on through a #, and any word in a
command's arguments does (`x-y#c`, `1#c`), also as the first token of a file,
after a block comment and after a pipe or `return` starts the command. A keyword
in a command's arguments, after a `-Parameter` of a dot-sourced script included,
is an argument, spelled with a capital, as `-or` there is a parameter name. A
stray closer leaves the outermost frame; a hashtable at the top level closes
before the function after it; a class inside a function ends at its own `}`.
"""
import re

import pytest

from crapkit import lizardpowershell
from crapkit.analyze import analyze_source
from crapkit.lizardpowershell import PowerShellReader

IF_BODY = "if ($a) { 1 }"


def _rows(source: str) -> list[tuple]:
    """(name, start, end, ccn, cognitive, nesting) of each function."""
    return [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive, r.nesting)
            for r in analyze_source("a.ps1", source, note=False)]


@pytest.mark.parametrize("line", [
    "Write-Host <#note#>a#b",   # a word glued to a block comment
    "x-y#c",                    # a one-letter verb
    "$x | Write-Host 1#c",      # a command after a pipe
    "$x | foreach 1#c",         # ForEach-Object's alias after a pipe
    "return Write-Output 1#c",  # a command after return
])
def test_a_hash_inside_a_word_leaves_the_rest_of_the_line_code(line):
    """The # is part of the word, so the `if` after the `;` still counts."""
    assert _rows(f"function f {{\n  {line}; {IF_BODY}\n}}\n") == [("f", 1, 3, 2, 1, 1)]


def test_a_hash_inside_the_file_s_first_word_leaves_its_line_code():
    assert _rows(f"x#y; function f {{ {IF_BODY} }}\n") == [("f", 1, 1, 2, 1, 1)]


@pytest.mark.parametrize("line", ["return Test-Path $a -or $b", ". ./run.ps1 -Mode if",
                                  ". $script -Encoding default -Mode foreach"])
def test_a_keyword_or_an_operator_in_a_command_s_arguments_counts_nothing(line):
    assert _rows(f"function f {{\n  {line}\n}}\n") == [("f", 1, 3, 1, 0, 0)]


def test_a_keyword_or_an_operator_in_a_command_s_arguments_is_spelled_with_a_capital():
    source = "git switch main\n$o | Out-File -Encoding default\nTest-Path $a -or $b\n"

    assert [t for t in PowerShellReader.generate_tokens(source) if not t.isspace()] == [
        "git", "Switch", "main", "$o", "|", "Out-File", "-Encoding", "Default",
        "Test-Path", "$a", "-Or", "$b"]


@pytest.mark.parametrize("source, rows", [
    (f"}}\nfunction f {{\n  {IF_BODY}\n}}\n", [("f", 2, 4, 2, 1, 1)]),
    (f"function f {{\n  {IF_BODY}\n}}\n)\nfunction g {{\n  {IF_BODY}\n}}\n",
     [("f", 1, 3, 2, 1, 1), ("g", 5, 7, 2, 1, 1)]),
])
def test_a_stray_closer_leaves_the_outermost_frame(source, rows):
    assert _rows(source) == rows


def test_a_function_after_a_hashtable_at_the_top_level_is_declared():
    """The hashtable's `}` closes its frame, so `function` starts a statement and
    is no key."""
    assert _rows(f"$defaults = @{{ a = 1 }}\nfunction f {{\n  {IF_BODY}\n}}\n") == [
        ("f", 2, 4, 2, 1, 1)]


@pytest.mark.parametrize("declaration", ["class A {}", "class A : B { [int] M() { return 1 } }"])
def test_a_class_inside_a_function_ends_at_its_own_brace(declaration):
    """The if after the class counts toward f, and f ends at its own `}`."""
    assert _rows(f"function f {{\n  {declaration}\n  {IF_BODY}\n}}\n") == [("f", 1, 4, 2, 1, 1)]


def test_a_name_with_an_underscore_after_its_first_letter_declares_a_function():
    assert _rows(f"function a_thing {{\n  {IF_BODY}\n}}\n") == [("a_thing", 1, 3, 2, 1, 1)]


def test_a_declaration_named_only_by_its_scope_keeps_its_body_s_braces():
    """`function script:{ }` parses, and the reader names no function after a bare
    scope: its `{ }` is a block, so f still ends at its own `}`."""
    source = (f"function f {{\n  function script:{{ }}\n  {IF_BODY}\n}}\n"
              f"function g {{\n  {IF_BODY}\n}}\n")

    assert _rows(source) == [("f", 1, 4, 2, 1, 1), ("g", 5, 7, 2, 1, 1)]


def test_a_param_word_with_no_list_leaves_the_body_s_brace_to_close_it():
    source = f"function f {{ param }}\nfunction g {{\n  {IF_BODY}\n}}\n"

    assert _rows(source) == [("f", 1, 1, 1, 0, 0), ("g", 2, 4, 2, 1, 1)]


def test_a_parameter_named_x_counts():
    """`$X` is a name like any other: three entries, three parameters."""
    [row] = analyze_source("a.ps1", "function f {\n  param($X, ${Y}, $Z)\n}\n", note=False)

    assert row.params == 3


@pytest.mark.parametrize("text", ["\n$(Get-Date)\n", "$(Get-Date)\nmore\n"])
def test_the_newlines_around_a_subexpression_in_a_string_keep_their_lines(text):
    source = f'function f {{\n  $x = "{text}"\n  {IF_BODY}\n}}\nfunction g {{\n  {IF_BODY}\n}}\n'

    assert _rows(source) == [("f", 1, 6, 2, 1, 1), ("g", 7, 9, 2, 1, 1)]


def test_the_subexpression_pattern_nests_parens_to_the_depth_asked():
    """_parens(2) matches what sits in a `( )` holding one more pair and no third."""
    two = r"\(" + lizardpowershell._parens(2) + r"\)"

    assert re.fullmatch(two, "(a (b) c)") is not None
    assert re.fullmatch(two, "(a (b (c)) d)") is None
    assert lizardpowershell._parens(1) == r"[^()]*+"
