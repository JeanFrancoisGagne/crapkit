"""The shell reader at the edges of its heuristics, every number hand-counted.

bash(1) "Here Documents": the delimiter line must be the word alone, `<<-` strips
leading tabs (tabs only) from it, and a `<<` that is quoted or sits inside
`$(( ))` opens nothing. The module docstring's own rules: a quoted `<<` and an
arithmetic `<<` open nothing, an opener with no terminator below it opens
nothing, a body counts 0 NLOC, a function body is `{ }` or `( )`, and keywords
never name a function.

Each script below marks whether a heredoc opened by what happens to `probe() {
if a; then b; fi }` in its body: blanked, it is gone; left as code, it is
reported with ccn 2.
"""
import lizard
import lizard_languages
import pytest

from crapkit import lizardshell

PROBE = "probe() {\n  if a; then b; fi\n}\n"


def functions(code: str) -> list[tuple]:
    analyzer = lizard.FileAnalyzer(lizard.get_extensions([]))
    found = analyzer.analyze_source_code("edge.sh", code).function_list
    return [(fn.name, fn.start_line, fn.end_line, fn.cyclomatic_complexity) for fn in found]


def opened(first_line: str) -> bool:
    """Whether FIRST_LINE opens a heredoc whose body holds PROBE: `probe` is gone."""
    code = f"{first_line}\n{PROBE}EOF\nbits\n"
    return ("probe", 2, 4, 2) not in functions(code)


@pytest.mark.parametrize("line, opens", [
    ('echo "pipe it <<EOF"', False),
    ('echo "a" "b <<EOF"', False),
    ('cat "file" <<EOF', True),
    ("echo 'pipe it <<EOF'", False),
    ("echo 'a' 'b <<EOF'", False),
    ("cat 'file' <<EOF", True),
    ('cat <<EOF "trailing', True),
    ("cat <<EOF 'trailing", True),
])
def test_a_quote_before_the_arrows_decides_whether_they_open_a_body(line, opens):
    assert opened(line) is opens


@pytest.mark.parametrize("line, opens", [
    ("(( x = 1 <<EOF ))", False),
    ("n=$(( 1 <<bits ))", False),
    ("(( a )) ; cat <<EOF", True),
    ("cat <<EOF ; (( n = 2 ))", True),
    ("cat <<EOF ; n=$((", True),
])
def test_arrows_inside_open_arithmetic_are_a_shift(line, opens):
    assert opened(line) is opens


@pytest.mark.parametrize("terminator, ends", [
    ("EOF", True), ("EOF ", False), ("\tEOF", False), ("EOF\r", True)])
def test_a_plain_body_ends_only_on_the_delimiter_alone(terminator, ends):
    """A line that is not the delimiter is body: the probe after it stays blank."""
    code = f"cat <<EOF\n{terminator}\n{PROBE}EOF\n"

    assert (("probe", 3, 5, 2) in functions(code)) is ends


@pytest.mark.parametrize("terminator, ends", [("\t\tEOF", True), ("  EOF", False)])
def test_a_dash_body_strips_tabs_only_from_its_delimiter(terminator, ends):
    code = f"cat <<-EOF\n{terminator}\n{PROBE}EOF\n"

    assert (("probe", 3, 5, 2) in functions(code)) is ends


@pytest.mark.parametrize("source, stripped", [
    # a body with no lines: the delimiter on the very next line closes it, and is blanked
    ("cat <<EOF\nEOF\necho\n", "cat <<EOF\n\necho\n"),
    # a body that ends the file with no line break: blanked to nothing, so no line is added
    ("cat <<EOF\nx\nEOF", "cat <<EOF\n\n"),
    # a delimiter ending in X, and a dash delimiter starting with X: every letter is the word's
    ("cat <<BOX\nif\nBOX\n", "cat <<BOX\n\n\n"),
    ("cat <<-XY\n\tif\n\tXY\n", "cat <<-XY\n\n\n"),
])
def test_the_stripper_blanks_each_body_line_and_keeps_the_line_count(source, stripped):
    assert lizardshell._HeredocStripper().strip(source) == stripped


def test_a_slash_star_glob_gets_a_space_and_nothing_else():
    assert lizardshell._defuse_block_comments("ls /* /*/x") == "ls / * / */x"


def test_a_delimiter_above_the_opener_does_not_close_it():
    """Only a terminator below the opener makes it one."""
    code = f"EOF\ncat <<EOF\n{PROBE}"

    assert functions(code) == [("probe", 3, 5, 2)]


def test_a_body_inside_a_function_counts_no_lines_of_code():
    code = "emit() {\n  cat <<EOF\n  one\n  two\nEOF\n  echo done\n}\n"
    analyzer = lizard.FileAnalyzer(lizard.get_extensions([]))
    (fn,) = analyzer.analyze_source_code("edge.sh", code).function_list

    assert (fn.start_line, fn.end_line, fn.nloc) == (1, 7, 4)


@pytest.mark.parametrize("header", ["function time", "function if", '"$name"()'])
def test_a_keyword_or_a_dynamic_word_names_no_function(header):
    assert functions(f"{header} {{\n  if a; then b; fi\n}}\n") == []


def test_function_keyword_with_parentheses_is_one_header():
    assert functions("function f() {\n  if a; then b; fi\n}\n") == [("f", 1, 3, 2)]


def test_a_subshell_body_closes_on_its_parenthesis():
    code = "f() (\n  if a; then b; fi\n)\ng() {\n  :\n}\n"

    assert functions(code) == [("f", 1, 3, 2), ("g", 4, 6, 1)]


def test_a_nested_brace_group_closes_before_its_function():
    code = "f() {\n  { a; }\n  if b; then c; fi\n}\ng() {\n  :\n}\n"

    assert functions(code) == [("f", 1, 4, 2), ("g", 5, 7, 1)]


def test_a_header_without_a_body_hands_its_next_word_back():
    """`f()` then `if`: bash allows any compound command as a body, this reader
    only `{ }` and `( )`, so f is not reported, and the words after it are read
    again from the start: the second header still is one."""
    code = "f() if a; then b; fi\nfunction g {\n  :\n}\nfunction h\nfunction k {\n  :\n}\n"

    assert functions(code) == [("g", 2, 4, 1), ("k", 6, 8, 1)]


def test_arithmetic_counts_its_question_mark_and_nothing_after_it():
    """`?` decides inside `(( ))` only; after `))` it is a glob again."""
    code = "f() {\n  (( x ? y : z ))\n  ls a?b\n}\n"

    assert functions(code) == [("f", 1, 4, 2)]


def test_registering_on_a_stock_lizard_appends_the_reader(monkeypatch):
    monkeypatch.setattr(lizard_languages, "languages", lizardshell._stock_languages)

    assert lizardshell.register() is lizardshell.ShellReader
    assert lizard_languages.languages() == lizardshell._stock_languages() + [
        lizardshell.ShellReader]
