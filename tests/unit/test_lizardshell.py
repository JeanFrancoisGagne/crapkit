"""The shell reader, pinned at the seam lizard resolves and the seam crapkit calls.

Every number in this file is hand-counted from the source above it. lizard has no
shell support at all, so nothing here is a regression guard against an upstream
change: it is the specification.
"""
import pathlib

import lizard
import lizard_languages
import pytest

from crapkit.analyze import ANALYSIS_VERSION, analyze_source
from crapkit.lizardshell import ShellReader, register

# base 1, + if, elif, for, &&, ||, while, until
PROBE = '''deploy() {
  if [ -z "$1" ]; then
    return 1
  elif [ "$1" = "all" ]; then
    for host in $HOSTS; do
      ping "$host" && echo up || echo down
    done
  fi
  while read -r line; do
    echo "$line"
  done < "$1"
  until ok; do
    sleep 1
  done
}
'''
PROBE_CCN = 8


def _functions(code, name="probe.sh"):
    """Through lizard's own pipeline, so the reader is reached the way lizard
    reaches it: filename -> get_reader_for -> tokenize -> extensions -> reader."""
    analyzer = lizard.FileAnalyzer(lizard.get_extensions([]))
    return analyzer.analyze_source_code(name, code).function_list


def _only(code, name="probe.sh"):
    (fn,) = _functions(code, name)
    return fn


# --- registration: lizard has to resolve .sh to this reader ---------------------

def test_lizard_resolves_sh_to_the_shell_reader():
    assert lizard_languages.get_reader_for("deploy.sh") is ShellReader


def test_lizard_resolves_bash_to_the_shell_reader():
    assert lizard_languages.get_reader_for("deploy.bash") is ShellReader


def test_lizard_shipped_no_reader_for_sh_at_all():
    from crapkit.lizardshell import _stock_languages

    assert [r for r in _stock_languages() if r.match_filename("deploy.sh")] == []


def test_register_is_idempotent():
    before = len(lizard_languages.languages())
    register()
    register()
    assert len(lizard_languages.languages()) == before


def test_registration_leaves_the_stock_readers_alone():
    assert lizard_languages.get_reader_for("a.py").language_names == ["python"]


def test_what_a_shell_script_costs_when_it_is_read_as_c():
    """The price of skipping registration, since lizard answers rather than fails:
    `(get_reader_for(filename) or CLikeReader)`. CLikeReader accepts `f() { }`
    because it looks like C, so the wrong answer arrives shaped like a right one.
    The probe loses two of its eight branches, `elif` and `until` being no part of
    C, and the `function name { }` spelling disappears entirely."""
    assert [f.cyclomatic_complexity for f in _functions(PROBE, "probe.c")] == [6]
    assert [f.name for f in _functions(BOTH, "both.c")] == ["beta", "gamma", "delta"]


# --- ccn convention ------------------------------------------------------------

def test_hand_counted_ccn_of_the_probe():
    assert _only(PROBE).cyclomatic_complexity == PROBE_CCN


def test_the_probe_is_one_function_named_deploy():
    fn = _only(PROBE)
    assert (fn.name, fn.long_name, fn.start_line, fn.end_line) == ("deploy", "deploy()", 1, 15)


def test_pipes_are_not_conditions():
    """'|' is data flow, not a branch: lizard counts '&&'/'||' and nothing else."""
    code = 'piped() {\n  ps aux | grep ssh | wc -l\n}\n'
    assert _only(code).cyclomatic_complexity == 1


# --- the case decision: arms, not the keyword ----------------------------------

DISPATCH = '''dispatch() {
  case "$1" in
    start) do_start ;;
    stop) do_stop ;;
    *) usage ;;
  esac
}
'''


def test_a_three_arm_case_counts_three():
    """Hand count: base 1, plus one per ';;'. The 'case' keyword itself is free."""
    assert _only(DISPATCH).cyclomatic_complexity == 4


def test_the_case_keyword_alone_costs_nothing():
    code = 'empty() {\n  case "$1" in\n  esac\n}\n'
    assert _only(code).cyclomatic_complexity == 1


def test_a_last_arm_written_without_its_terminator_is_the_documented_undercount():
    """POSIX lets the arm before `esac` drop its ';;'. Two arms, one terminator,
    so this scores 2 where the three-arm case above scores 4."""
    code = 'bias() {\n  case "$1" in\n    a) one ;;\n    b) two\n  esac\n}\n'
    assert _only(code).cyclomatic_complexity == 2


# --- hazard: heredoc bodies ----------------------------------------------------

HEREDOC = '''emit() {
  cat <<EOF
if true; then
  helper() {
    echo "$x" && echo more
  }
fi
EOF
  echo done
}
'''


def test_a_heredoc_body_contributes_no_conditions_and_no_functions():
    fn = _only(HEREDOC)
    assert (fn.name, fn.cyclomatic_complexity) == ("emit", 1)


def test_a_heredoc_body_keeps_the_lines_after_it_on_the_right_numbers():
    """The body's tokens are dropped, its newlines are not."""
    assert _only(HEREDOC).end_line == 10


def test_a_dash_heredoc_ends_on_a_tab_indented_terminator():
    code = 'emit() {\n\tcat <<-EOF\n\tif x; then y; fi\n\tEOF\n\techo out\n}\n'
    fn = _only(code)
    assert (fn.name, fn.cyclomatic_complexity, fn.end_line) == ("emit", 1, 6)


def test_a_quoted_heredoc_delimiter_opens_a_heredoc_like_any_other():
    code = ("emit() {\n  cat <<'EOF'\n  for i in 1 2; do :; done\n"
            "  helper() { :; }\nEOF\n}\n")
    fn = _only(code)
    assert (fn.name, fn.cyclomatic_complexity) == ("emit", 1)


def test_a_herestring_is_not_a_heredoc():
    """'<<<' feeds one word on the same line; reading it as '<<' would swallow
    the rest of the file looking for a terminator that never comes."""
    code = ('grepit() {\n  grep -q x <<<"$1" && echo yes\n}\n\n'
            'later() {\n  if x; then y; fi\n}\n')
    assert [(f.name, f.cyclomatic_complexity) for f in _functions(code)] == [
        ("grepit", 2), ("later", 2)]


def test_a_left_shift_inside_arithmetic_is_not_a_heredoc():
    """'$(( 1 << bits ))' names a variable right where a delimiter would sit."""
    code = ('shift_bits() {\n  local n=$(( 1 << bits ))\n  echo "$n"\n}\n\n'
            'later() {\n  if x; then y; fi\n}\n')
    assert [f.name for f in _functions(code)] == ["shift_bits", "later"]


# Characters `str.splitlines` ends a line at and bash does not: bash ends a heredoc
# line at LF only.
NOT_LINE_ENDS = ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


@pytest.mark.parametrize("mark", NOT_LINE_ENDS, ids=[f"U+{ord(c):04X}" for c in NOT_LINE_ENDS])
def test_a_delimiter_after_a_form_feed_does_not_end_the_heredoc(mark):
    """bash prints `note<FF>EOF` and the `if` line below it as text, and ends the
    body at the bare `EOF`. Split at the form feed, the body closed one line early
    and the `if` and `&&` counted as code."""
    code = f"emit() {{\n  cat <<EOF\nnote{mark}EOF\nif a && b; then c; fi\nEOF\n}}\n"
    fn = _only(code)
    assert (fn.name, fn.cyclomatic_complexity, fn.end_line) == ("emit", 1, 6)


@pytest.mark.parametrize("mark", NOT_LINE_ENDS, ids=[f"U+{ord(c):04X}" for c in NOT_LINE_ENDS])
def test_code_after_a_form_feed_on_the_opener_line_stays_code(mark):
    """bash runs the `if` on the line that opens the heredoc; the body starts on
    the next line. Split at the form feed, the `if` read as the body's first line."""
    code = (f'emit() {{\n  cat <<EOF; echo "page{mark}"; if x; then y; fi\n'
            f"body\nEOF\n}}\n")
    fn = _only(code)
    assert (fn.name, fn.cyclomatic_complexity, fn.end_line) == ("emit", 2, 5)


def test_a_heredoc_inside_a_quoted_substitution_is_a_body():
    """`v="$(node - "$f" <<'JS'` opens a body. The `"` before `$(` quotes the
    substitution's output, and the command's own quotes pair among themselves.
    Read as a string instead, the program's `if` and `||` counted as shell once
    the substitution was read as code. The body counts 0 NLOC: lines 1, 2, 5, 6
    and 7 do."""
    code = ('emit() {\n  v="$(node - "$f" <<\'JS\'\nif (a || b) { run(); }\nJS\n  )"\n'
            '  echo "$v"\n}\n')
    fn = _only(code)
    assert (fn.name, fn.cyclomatic_complexity, fn.end_line, fn.nloc) == ("emit", 1, 7, 5)


@pytest.mark.parametrize("line", ['echo "pipe it <<EOF"', 'echo "$(date) <<EOF"'])
def test_a_heredoc_opener_inside_a_string_opens_no_body(line):
    """The `<<` sits in the string, after any substitution in it has closed, so
    the `EOF` line below ends nothing and the `if` between them counts."""
    code = 'emit() {\n  ' + line + '\n  if x; then y; fi\n}\nEOF\n'
    assert _only(code).cyclomatic_complexity == 2


# A line can start inside a string, a substitution or a case that an earlier line
# opened. Counted one line at a time, the quotes on the line alone decided.

def test_a_heredoc_on_the_line_that_closes_a_quoted_substitution_is_a_body():
    """Line 4's first `"` closes the string line 2 opened, so its `<<'JS'` sits in
    code. Counted on line 4 alone, three quotes preceded it, it read as quoted,
    and the program's `if`, `||`, `if` and `&&` counted as shell. The body and
    its terminator count 0 NLOC: lines 1 to 4, 8 and 9 do."""
    code = ('load() {\n  X="$(\n    printf x\n  )" node - "$p" <<\'JS\'\n'
            'if (a || b) { c(); }\nif (d && e) { f(); }\nJS\n  echo done\n}\n')
    fn = _only(code)
    assert (fn.cyclomatic_complexity, fn.end_line, fn.nloc) == (1, 9, 6)


def test_a_heredoc_opener_on_the_second_line_of_a_string_opens_no_body():
    """`cat <<EOF` on line 3 is text inside the string line 2 opened, so the
    `EOF` line below ends nothing and the `if` between them counts."""
    code = ('emit() {\n  echo "usage:\n  cat <<EOF\n"\n  if x; then y; fi\n}\nEOF\n')
    assert _only(code).cyclomatic_complexity == 2


def test_a_left_shift_in_a_multi_line_single_quoted_program_is_not_a_heredoc():
    code = ("calc() {\n  awk '\n    { print x << y }\n  '\n  if a; then b; fi\n}\ny\n")
    assert _only(code).cyclomatic_complexity == 2


def test_a_heredoc_after_a_case_pattern_inside_a_quoted_substitution_is_a_body():
    """The pattern's `)` does not close the `$(`, so the `<<'E'` after it sits in
    the substitution, not in the string around it. Base 1 + one arm = 2."""
    code = ("f() {\n  v=\"$(case $k in a) cat <<'E'\nif (a || b) { c(); }\nE\n"
            "  ;; esac)\"\n}\n")
    fn = _only(code)
    assert (fn.cyclomatic_complexity, fn.end_line) == (2, 6)


@pytest.mark.parametrize("line", [
    "# it's a comment", "echo $# args", 'echo "${#list[@]}"', "echo 'a' \\' b",
    "x=$'it\\'s'"])
def test_a_quote_that_opens_nothing_leaves_the_next_heredoc_alone(line):
    """None of these lines leaves a quote open: a comment's apostrophe, `$#`,
    `${#...}`, an escaped quote and `$'...'`. The heredoc below each is a body."""
    code = 'emit() {\n  ' + line + '\n  cat <<EOF\nif x; then y; fi\nEOF\n}\n'
    assert _only(code).cyclomatic_complexity == 1


# --- hazard: quotes and comments -----------------------------------------------

def test_keywords_inside_quotes_are_not_conditions():
    code = ('quoted() {\n  echo "if for while && ||"\n'
            "  echo 'if elif until ;;'\n}\n")
    assert _only(code).cyclomatic_complexity == 1


def test_keywords_inside_comments_are_not_conditions():
    code = ('commented() {\n  # if for while && || ;;\n'
            '  echo hi  # elif until\n}\n')
    assert _only(code).cyclomatic_complexity == 1


def test_a_commented_out_function_is_not_a_function():
    code = 'real() {\n  echo hi\n}\n# fake() {\n#   echo no\n# }\n'
    assert [f.name for f in _functions(code)] == ["real"]


def test_a_parameter_expansion_hash_does_not_start_a_comment():
    """'${PATH#/usr}' and '$#' both put a '#' mid-line; read as a comment either
    would swallow the '}' that closes the function."""
    code = ('trim() {\n  local p=${PATH#/usr}\n  [ $# -gt 0 ] && echo "$p"\n}\n\n'
            'later() {\n  echo hi\n}\n')
    assert [(f.name, f.cyclomatic_complexity) for f in _functions(code)] == [
        ("trim", 2), ("later", 1)]


# --- hazard: parens and braces that are not function syntax ---------------------

def test_command_substitution_does_not_open_a_function():
    code = 'subst() {\n  local now\n  now=$(date +%s)\n  echo "$now"\n}\n'
    assert [f.name for f in _functions(code)] == ["subst"]


def test_a_subshell_after_a_command_word_does_not_open_a_function():
    code = 'echo start\n(cd /tmp && make)\necho done\n'
    assert _functions(code) == []


def test_a_subshell_inside_a_function_stays_inside_it():
    code = 'sub() {\n  (cd /tmp && make)\n  echo done\n}\n'
    fn = _only(code)
    assert (fn.name, fn.cyclomatic_complexity, fn.end_line) == ("sub", 2, 4)


def test_an_array_assignment_does_not_open_a_function():
    code = 'arr() {\n  local values=()\n  values+=(a b)\n}\n'
    assert [f.name for f in _functions(code)] == ["arr"]


def test_a_brace_group_is_not_a_function():
    code = '{ echo a; echo b; } > out.txt\n'
    assert _functions(code) == []


def test_a_brace_group_inside_a_function_does_not_end_it_early():
    code = 'grouped() {\n  { echo a; echo b; } > out\n  echo after\n}\n'
    fn = _only(code)
    assert (fn.name, fn.end_line) == ("grouped", 4)


# --- both function syntaxes ----------------------------------------------------

BOTH = '''function alpha {
  if x; then y; fi
}

function beta() {
  echo hi
}

gamma() {
  echo hi
}

delta ()
{
  echo hi
}
'''


def test_all_four_function_spellings_are_reported():
    assert [(f.name, f.start_line) for f in _functions(BOTH)] == [
        ("alpha", 1), ("beta", 5), ("gamma", 9), ("delta", 13)]


def test_a_subshell_bodied_function_is_reported():
    code = 'isolated() (\n  cd /tmp && make\n)\n'
    fn = _only(code)
    assert (fn.name, fn.cyclomatic_complexity, fn.end_line) == ("isolated", 2, 3)


def test_a_case_pattern_does_not_close_a_subshell_bodied_function():
    """A pattern ends in a bare `)`, and the body's own `)` comes after `esac`.
    Read as the body's close, the first pattern ended `pick` on line 3 with ccn 1,
    and both arms fell outside it. Base 1 + two arms = 3, lines 1 to 6."""
    code = ('pick() (\n  case "$1" in\n    a) echo a;;\n    (b) echo b;;\n  esac\n)\n'
            'after() {\n  if x; then :; fi\n}\n')
    assert [(f.name, f.start_line, f.end_line, f.cyclomatic_complexity)
            for f in _functions(code)] == [("pick", 1, 6, 3), ("after", 7, 9, 2)]


def test_a_subshell_inside_a_case_arm_still_closes_where_it_should():
    """Only a pattern's `)` is skipped: the `( cd x )` inside an arm pairs with its
    own `(`, and a `case` word that no `in` follows opens nothing."""
    code = ('pick() (\n  echo case\n  case $v in\n    a) ( cd x && ls );;\n  esac\n)\n'
            'after() {\n  echo hi\n}\n')
    assert [(f.name, f.end_line, f.cyclomatic_complexity) for f in _functions(code)] == [
        ("pick", 6, 3), ("after", 9, 1)]


# --- only functions, never top-level code --------------------------------------

REAL_SHAPED = '''#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  echo "usage: $0 [--force]" >&2
  exit 2
}

deploy() {
  local target="$1"
  if [ -z "$target" ]; then
    usage
  fi
  for host in $HOSTS; do
    ssh "$host" true && echo ok || echo fail
  done
}

if [ "$#" -eq 0 ]; then
  usage
fi

case "$1" in
  deploy) deploy "$2" ;;
  *) usage ;;
esac
'''


def test_only_the_functions_of_a_real_shaped_script_are_reported():
    """Top-level code belongs to lizard's '*global*' pseudo-function, which never
    reaches the function list, exactly like Python module-level code."""
    assert [(f.name, f.cyclomatic_complexity) for f in _functions(REAL_SHAPED)] == [
        ("usage", 1), ("deploy", 5)]


# --- hazard: C comment openers that are shell globs and paths ------------------

GLOB_COMMENT = '''resolve() {
  case "$1" in
    /*)
      echo "$1"
      ;;
  esac
}

trailing() {
  echo "${1%*/}"
}

later() {
  echo hi
}
'''


def test_a_slash_star_glob_is_not_a_block_comment():
    """'/*' opens a C block comment in lizard's shared token pattern and is the
    glob for an absolute path in shell. The block-comment alternative sits ahead
    of the one place a reader may extend that pattern, so it has to be undone
    afterwards: here it would run to the '*/' inside '${1%*/}' and eat two
    closing braces on the way. Found on the consumer repo's install-cli.sh, where
    it hid 55 of 59 functions."""
    assert [f.name for f in _functions(GLOB_COMMENT)] == ["resolve", "trailing", "later"]


def test_a_double_slash_in_a_url_is_not_a_line_comment():
    code = 'fetch() {\n  curl http://example.com && echo ok\n}\n'
    assert _only(code).cyclomatic_complexity == 2


def test_an_escaped_quote_outside_a_string_does_not_open_one():
    r"""A backslash escapes the next character in shell, so `\"` is a literal
    quote, not a string opener. Read as an opener it eats to the next real quote,
    taking `esac`, a closing brace and a whole function with it. Found on the
    consumer repo's test-live-acp-bind-docker.sh."""
    code = ('quoted() {\n  case "$v" in\n    \\"*\\") echo q ;;\n  esac\n}\n\n'
            'later() {\n  echo "hi"\n}\n')
    assert [f.name for f in _functions(code)] == ["quoted", "later"]


NESTED_QUOTES = ('v="$(node -e \'const p = JSON.parse(read("pkg", "utf8"));\' "$f")"\n')


def test_a_command_substitution_inside_double_quotes_keeps_its_quotes_paired():
    """lizard's shared string rule ends at the first inner quote, and every quote
    after it pairs off by one until a brace lands inside a string; on the consumer
    repo's install.sh that hid 18 of 153 functions. The run is matched whole, so
    its inner quotes pair with each other, and only then is its command read as
    code: the single-quoted program and `"$f"` arrive whole, beside `node`."""
    tokens = set(ShellReader.generate_tokens(NESTED_QUOTES))

    assert {"node", "'const p = JSON.parse(read(\"pkg\", \"utf8\"));'", '"$f"'} <= tokens


def test_a_plain_double_quoted_string_is_still_one_token():
    """The same alternative handles the ordinary case, or it would be a regression
    dressed as a fix."""
    assert '"hello world"' in list(ShellReader.generate_tokens('echo "hello world"\n'))


# --- code inside a string: a substitution in "..." or in ${...} ----------------
#
# `"$(a && b)"` runs `a && b`. The quotes keep its output one word; they do not
# make the command text. Each line below holds one decision, the `&&`, `||` or
# `if` inside the hole, and reads what the same command reads written bare.

HOLES = {
    "double quotes": ('x="$(a && b)"', "x=$(a && b)"),
    "text around it": ('x="v: $(a || b) end"', "x=$(a || b)"),
    "one inside another": ('x="$(c "$(a && b)")"', "x=$(c $(a && b))"),
    "backticks": ('x="`a && b`"', "x=`a && b`"),
    "arithmetic": ('x="$(( a && b ))"', "x=$(( a && b ))"),
    "a default": ("x=${y:-$(a && b)}", "x=$(a && b)"),
    "a quoted default": ('x="${y:-$(a && b)}"', "x=$(a && b)"),
    "an if": ('x="$(if a; then b; fi)"', "x=$(if a; then b; fi)"),
    "a case": ('x="$(case $y in a) b;; esac)"', "x=$(case $y in a) b;; esac)"),
    "a case with a quote in an arm": ('x="$(case "$y" in a) echo "b";; esac)"',
                                      'x=$(case "$y" in a) echo "b";; esac)'),
    "eight levels of parens": ('x="$(' + " (" * 7 + " a && b" + " )" * 8 + '"',
                               "x=$(" + " (" * 7 + " a && b" + " )" * 8),
}


def _columns(line):
    (record,) = analyze_source("hole.sh", "f() {\n  " + line + "\n}\n")
    return record.ccn_std, record.ccn, record.cognitive, record.nesting


@pytest.mark.parametrize("quoted, bare", HOLES.values(), ids=HOLES.keys())
def test_a_command_inside_a_string_counts_what_it_counts_bare(quoted, bare):
    """NIST SP 500-235 sec. 4.1: the decision counts wherever its command sits,
    so ccn_std is 2 in both spellings."""
    assert (_columns(quoted), _columns(quoted)[0]) == (_columns(bare), 2)


def test_an_escaped_dollar_in_double_quotes_is_text():
    """`"\\$(a && b)"` prints `$(a && b)` and runs nothing."""
    assert _columns('x="\\$(a && b)"')[:2] == (1, 1)


def test_a_substitution_in_single_quotes_is_text():
    assert _columns("x='$(a && b)'")[:2] == (1, 1)


def test_a_substitution_over_two_lines_keeps_every_later_line_number():
    """The hole's newline arrives as its own token instead of inside a string's,
    so a function below it still starts and ends where it does."""
    code = ('first() {\n  x="$(a &&\n    b)"\n}\n\n'
            'second() {\n  echo "$x"\n}\n')
    spans = [(r.start, r.end, r.ccn) for r in analyze_source("two.sh", code)]

    assert spans == [(1, 4, 2), (6, 8, 1)]


def test_a_case_inside_a_quoted_substitution_closes_at_its_esac():
    """Each arm's pattern ends in a bare `)`. Taken as the substitution's close,
    the first one cut the hole at `Linux)`: `case` reached the counters and `esac`
    stayed in the string, so the level never closed and both ifs after it paid
    one level too many. Written bare, the line reads ccn_std 1 + two arms + two
    ifs = 5, cognitive switch 1 + two ifs = 3, nesting 1 (Sonar B2)."""
    code = ('f() {\n  os="$(case "$(uname -s)" in Linux) echo linux;; Darwin) echo mac;; esac)"\n'
            '  if a; then :; fi\n  if b; then :; fi\n}\n')
    (record,) = analyze_source("case.sh", code)

    assert (record.ccn_std, record.cognitive, record.nesting) == (5, 3, 1)


def test_a_brace_in_a_quoted_case_arm_hides_no_function():
    """`"$(case $x in a) echo "a{";; esac)"` is one string in shell. Cut at `a)`,
    its `"a{"` paired off wrong and the `{` left in code swallowed `g`."""
    code = ('f() {\n  v="$(case $x in a) echo "a{";; b) echo b;; esac)"\n}\n\n'
            'g() {\n  return 1\n}\n')
    assert [(r.long_name, r.start, r.end, r.ccn_std)
            for r in analyze_source("arm.sh", code)] == [("f()", 1, 3, 3), ("g()", 5, 7, 1)]


def test_parens_nine_levels_deep_are_the_documented_limit():
    """The string rule reads eight levels of parens inside a substitution. At
    nine it does not match, the string ends at its first inner quote as lizard's
    own rule ends it, and the `&&` counts nothing."""
    line = 'x="$(' + " (" * 8 + " a && b" + " )" * 9 + '"'
    assert _columns(line)[:2] == (1, 1)


def test_an_unpaired_quote_before_many_substitutions_reads_in_linear_time():
    """With no closing quote left in the file, the string rule used to try every
    way of reading each `$( )` after it as a substitution or as text: two ways per
    substitution, 7 s for 24 of them and twice that for each one more. Its loops
    no longer give back what they matched, so this tokenizes at once; before, it
    did not finish."""
    code = 'f() {\n  x="' + " $(a)" * 40 + "\n}\n"
    assert [(f.name, f.end_line) for f in _functions(code)] == [("f", 3)]


# --- a reserved word is one only where a command starts ------------------------
#
# POSIX XCU 2.4: shell reads `if`, `done` and the other reserved words as reserved
# only first in a command, straight after another reserved word, as the `do` after
# a for's name and as the `esac` where a case pattern would start, and only when a
# blank or an operator ends them. Anywhere else they are words: `echo done` prints
# "done". Each line below sits in a loop ahead of an if, so the function reads
# ccn_std 3 (for, if), cognitive 3 (for +1, if +1 and +1 for its nesting) and
# nesting 2 whatever the line holds (Sonar B2).

IN_A_LOOP = ('f() {{\n  for x in a b; do\n    {line}\n    if [ -n "$x" ]; then\n'
             '      echo "$x"\n    fi\n  done\n}}\n')

ARGUMENTS = {
    "a done argument": "echo done",
    "closers": "echo fi esac",
    "openers": "echo if case for while until select",
    "a case and its in": "echo case x in y",
    "a for inside a word": "git for-each-ref",
    "a do inside a command's own name": "do-thing",
    "an assignment": "done=1",
    "a variable": "echo $done",
    "a test operand": '[ "$a" = done ]',
    "an array": "arr=(if then done)",
    "a continued line": "cmd \\\n      done",
    "after a redirection": "cmd 2>&1 >/dev/null done",
}


@pytest.mark.parametrize("line", ARGUMENTS.values(), ids=ARGUMENTS.keys())
def test_a_reserved_word_that_starts_no_command_is_a_word(line):
    """`echo done` closed the loop, so the if after it paid no nesting;
    `echo if` and `git for-each-ref` each added a decision and a block."""
    (record,) = analyze_source("words.sh", IN_A_LOOP.format(line=line))

    assert (record.ccn_std, record.cognitive, record.nesting) == (3, 3, 2)


# Each body holds reserved words in a place where shell reads them as reserved.
# Hand values (ccn_std, cognitive, nesting), cognitive by Sonar B2.
COMMAND_STARTS = {
    # && if || while until for = 7; each +1 at nesting 0 = 6
    "after an operator": ("a && if b; then :; fi\n  c || while d; do :; done\n"
                          "  e | until g; do :; done\n  ! for x in y; do :; done", (7, 6, 1)),
    # if, while, until = 4; each +1 = 3
    "in a substitution, a subshell and a group": (
        "x=$(if a; then b; fi)\n  (while c; do :; done)\n  { until d; do :; done; }", (4, 3, 1)),
    # if if while for = 5; if 1, inner if 2, else 1, while 2, for 3 = 9
    "after then, else and do": ("if a; then if b; then :; fi; else while c; do "
                                "for x in y; do :; done; done; fi", (5, 9, 3)),
    # for if if = 4; for 1, inner if 2, if 1 = 4
    "a for with no in": ("for x do if a; then :; fi; done\n  if b; then :; fi", (4, 4, 2)),
    # for if = 3; for 1, if 2 = 3
    "an arithmetic for": ("for ((i = 0; i < 3; i++)) do if a; then :; fi; done", (3, 3, 2)),
    # for if = 3; for 1, if 2 = 3
    "after time": ("time for x in y; do if a; then :; fi; done", (3, 3, 2)),
    # two arms and two ifs = 5; case 1, if in an arm 2, if 1 = 4
    "patterns spelled like reserved words": (
        "case $1 in\n    (done|fi) if a; then :; fi ;;\n    *) b ;;\n  esac\n"
        "  if c; then :; fi", (5, 4, 2)),
    # if = 2; if 1
    "after a background &": ("a & if b; then :; fi", (2, 1, 1)),
    # `;&` falls through and ends no counted arm: one `;;` and the if = 3; case 1, if 2 = 3
    "a pattern after a fallthrough arm": (
        "case $1 in\n    a) if b; then :; fi ;&\n    done) c ;;\n  esac", (3, 3, 2)),
    # for, if = 3: the `;;` of `((;;))` ends no case arm; for 1, if 2 = 3
    "an arithmetic for with no condition": (
        "for ((;;)); do if a; then break; fi; done", (3, 3, 2)),
}


@pytest.mark.parametrize("body, expected", COMMAND_STARTS.values(), ids=COMMAND_STARTS.keys())
def test_a_reserved_word_where_a_command_starts_still_counts(body, expected):
    (record,) = analyze_source("starts.sh", "f() {\n  " + body + "\n}\n")

    assert (record.ccn_std, record.cognitive, record.nesting) == expected


def test_a_stray_close_paren_breaks_nothing_after_its_line():
    """Shell rejects `echo a )`; the reader reads past it, and the if on the next
    line still starts a command."""
    (record,) = analyze_source("stray.sh", "f() {\n  echo a )\n  if b; then :; fi\n}\n")

    assert (record.ccn_std, record.cognitive, record.nesting) == (2, 1, 1)


def test_an_empty_file_yields_no_token():
    assert list(ShellReader.generate_tokens("")) == []


# --- through crapkit's own analysis path ---------------------------------------

# base 1, + if, &&, for, inner if, while
SIX_BRANCH = '''probe() {
  if [ "$a" -gt 0 ] && [ "$b" -gt 0 ]; then
    for i in $(seq "$b"); do
      if [ "$i" = "$a" ]; then
        echo "$i"
      fi
    done
  else
    while [ "$b" -gt 0 ]; do
      b=$((b - 1))
    done
  fi
}
'''


def _record():
    (record,) = analyze_source("probe.sh", SIX_BRANCH)
    return record


def test_crapkit_reads_the_hand_counted_ccn_off_the_shell_reader():
    assert _record().ccn == 6


def test_a_six_branch_shell_function_gets_a_nonzero_cognitive_score():
    """The cognitive extension sits at index 0 of crapkit's chain, ahead of
    lizard's `preprocessing`. A reader that materialized the stream there would
    starve it and every function would read 0 (see
    tests/unit/test_cognitive_reader_chain.py); both of this reader's repairs are
    made to the source instead, so the stream stays a generator.

    10, hand-counted under the extension's rules: if 1, && 1, for 1+1, inner if
    1+2, else 1, while 1+1. `then`, `do`, `fi` and `done` cost nothing; `if`,
    `for` and `while` open a block and `fi` and `done` close it, so nesting rises
    inside a shell function the way it rises inside a braced one."""
    assert _record().cognitive == 10


def test_the_modified_column_does_not_cancel_the_case_arms():
    """crapkit takes min(ccn_std, ccn_mod), and lizard's modified rule subtracts 1
    for every token named 'case'. Counting arms as ';;' keeps 'case' out of the
    condition set, so the two columns agree and nothing is silently refunded."""
    (record,) = analyze_source("dispatch.sh", DISPATCH)
    assert (record.ccn_std, record.ccn_mod, record.ccn) == (4, 4, 4)


def test_bash_files_take_the_same_path_as_sh_files():
    assert analyze_source("probe.bash", SIX_BRANCH)[0].ccn == 6


# --- shell's word-delimited blocks, in the cognitive column --------------------

def _cognitive(name, source):
    (record,) = analyze_source(name, source)
    return record.cognitive


CASE_IN_IF = '''pick() {
  if [ -n "$1" ]; then
    case "$1" in
      a) echo a ;;
      b) echo b ;;
      *) echo z ;;
    esac
  fi
}
'''


def test_a_case_is_a_switch_and_its_arms_are_free():
    """The cognitive column and the ccn column disagree about a `case` on purpose.
    ccn counts the arms (three `;;` here) and charges the keyword nothing; the
    whitepaper charges a switch +1 and the nesting it sits in, and gives the arms
    nothing at all, exactly as it treats a C `case` label. 1 for the `if`, 2 for
    the case one level inside it, 0 for the three arms."""
    assert _cognitive("pick.sh", CASE_IN_IF) == 3


BRACE_GROUP = '''run() {
  if [ -n "$1" ]; then
    { echo a; echo b; } > /dev/null
    if [ -n "$2" ]; then
      echo c
    fi
  fi
}
'''


def test_a_brace_group_does_not_close_a_shell_block():
    """`fi` closes what `if` opened, and the `}` of a command group closes nothing.
    A shell block goes on the same nesting stack the brace rules use, so it is
    pushed as a marker no brace depth can equal: with a depth on the stack instead,
    the group's `}` would pop the outer `if` and the inner one would read 1+0."""
    assert _cognitive("run.sh", BRACE_GROUP) == 3


BREAK_PLAIN = '''scan() {
  for x in $1; do
    if [ "$x" = q ]; then
      break
    fi
  done
}
'''

BREAK_LEVELED = BREAK_PLAIN.replace("break", "break 2")


def test_a_bare_break_is_not_a_labeled_break():
    """Shell has no labels: a bare `break` leaves the nearest loop and is free,
    exactly as it is in TypeScript. The rule reads the token after break/continue,
    and every one of shell's is a block-closer word rather than the `;` or `}` a
    C-family bare break is followed by, so `break` before `fi` used to read as a
    label and cost a point no other language paid. 1 for, 2 inner if."""
    assert _cognitive("scan.sh", BREAK_PLAIN) == 3


def test_break_with_a_level_pays_the_labeled_jump():
    """`break 2` leaves two loops, which is the jump past the nearest enclosing
    one that a labeled break makes, and costs its +1."""
    assert _cognitive("scan.sh", BREAK_LEVELED) == 4


def test_a_glob_question_mark_decides_nothing():
    """`ls a?b` matches one character, so it is no decision in ccn and costs
    nothing in cognitive. The cognitive column read 1 per `?`, 2 here."""
    assert _cognitive("glob.sh", "f() {\n  ls a?b\n  echo ?\n}\n") == 0


# --- arithmetic: bash reads C inside (( )) and $(( )) ---------------------------

def _calc_columns(source):
    (record,) = analyze_source("calc.sh", source)
    return record.ccn_std, record.cognitive


@pytest.mark.parametrize("line", ["echo $(( x > 0 ? 1 : 0 ))",
                                  "(( y = x>0?1:0 ))",
                                  "echo $(( (a + b) > 0 ? 1 : 0 ))",
                                  "(( a > 0 ?\n     1 : 0 ))"])
def test_a_conditional_operator_in_arithmetic_is_one_decision(line):
    """Inside `(( ))` and `$(( ))` bash reads C, and `a ? b : c` is C's
    conditional operator: ccn 2 and cognitive 1, as the same line reads in C.
    ccn read 1 here, and cognitive 0 once the glob `?` stopped counting."""
    assert _calc_columns("f() {\n  " + line + "\n}\n") == (2, 1)


def test_the_shell_reader_counts_it_in_lizards_own_pipeline_too():
    assert _only("f() {\n  echo $(( x > 0 ? 1 : 0 ))\n}\n").cyclomatic_complexity == 2


def test_a_question_mark_between_two_arithmetic_expressions_is_a_glob():
    assert _calc_columns("f() {\n  echo $(( a ? 1 : 0 )) a?b $(( b ? 2 : 3 ))\n}\n") == (3, 2)


def test_two_subshells_are_no_arithmetic():
    """`( (cmd) )` has a space between its parentheses: two subshells, and the
    `?` inside a glob."""
    assert _calc_columns("f() {\n  ( (ls a?b) )\n}\n") == (1, 0)


def test_an_endless_c_style_for_counts_its_loop_once():
    """`for ((;;))` writes a loop's three empty clauses. Its `;;` read as a
    case arm's end, so the loop counted 2."""
    assert _calc_columns("f() {\n  for ((;;)); do\n    step\n  done\n}\n") == (2, 1)


def test_a_case_arm_after_arithmetic_still_counts():
    """case +1, the conditional inside it +1 and its nesting +1."""
    source = "f() {\n  case $1 in\n    a) (( n = n > 0 ? 1 : 0 )) ;;\n    b) ls x? ;;\n  esac\n}\n"

    assert _calc_columns(source) == (4, 3)


def test_analysis_version_invalidates_the_cached_shell_cognitive_column():
    """Every cached .sh and .bash record at version 7 or below carries a cognitive
    score measured with flat nesting, and the cache keys on content plus the
    analysis fingerprint. Shell is the only language whose stored values move."""
    assert ANALYSIS_VERSION > 7


# --- real scripts from the consumer repo ---------------------------------------
#
# Read, counted by hand, and asserted here. They are not vendored: crapkit is
# public and these are not its files, so the assertions run where the checkout
# exists and skip where it does not. The corpus is 97 scripts holding 475 function
# headers, of which the reader reports 462; the rest are defined inside heredoc
# bodies. All three tokenizer repairs in lizardshell.py came out of this sweep.
#
# These assert against files this repo does not own. When one of them is edited
# upstream the right response is to re-read it and update the numbers here, not to
# loosen the assertion: an exact count read off a real script is what caught every
# defect above.

CONSUMER_SCRIPTS = pathlib.Path(r"C:\Users\jfgag\openclaw\scripts")

needs_consumer_repo = pytest.mark.skipif(
    not CONSUMER_SCRIPTS.is_dir(), reason="consumer repo checkout not present")


def _real(name):
    analyzer = lizard.FileAnalyzer(lizard.get_extensions([]))
    return analyzer(str(CONSUMER_SCRIPTS / name)).function_list


@needs_consumer_repo
def test_claude_auth_status_reports_its_nine_functions():
    """Nine `name() {` headers, lines 19 to 111, no heredocs, no nesting."""
    assert [f.name for f in _real("claude-auth-status.sh")] == [
        "fetch_models_status_json", "calc_status_from_expires", "format_epoch_seconds",
        "json_expires_for_claude_cli", "json_expires_for_anthropic_any",
        "json_best_anthropic_profile", "json_anthropic_api_key_count",
        "check_claude_code_auth", "check_openclaw_auth"]


@needs_consumer_repo
def test_claude_auth_status_hand_counted_ccn():
    """check_openclaw_auth, lines 111-142: base 1, if(112), if(115), if(121),
    &&(121), if(130), ||(139) = 7. The `||` inside the multi-line jq program at
    136-139 is quoted and does not count; the one after the closing quote does."""
    by_name = {f.name: f for f in _real("claude-auth-status.sh")}
    assert by_name["check_openclaw_auth"].cyclomatic_complexity == 7
    assert (by_name["check_openclaw_auth"].start_line,
            by_name["check_openclaw_auth"].end_line) == (111, 142)


@needs_consumer_repo
def test_create_dmg_reports_seven_functions_around_a_heredoc():
    """Seven functions; an `osascript <<EOF` at line 198 whose body carries `if
    exists file ... then`, `end if` and a brace pair; and single-quoted awk
    programs at 104 and 109 whose `{ ... }` must not open a block."""
    assert [(f.name, f.cyclomatic_complexity) for f in _real("create-dmg.sh")] == [
        ("require_integer_list", 6), ("require_positive_integer", 2),
        ("require_nonnegative_integer", 3), ("to_applescript_list4", 1),
        ("to_applescript_pair", 1), ("cleanup_dmg", 6), ("detach_dmg", 5)]


@needs_consumer_repo
def test_ci_hydrate_live_auth_counts_around_its_herestrings():
    """Three functions. append_profile_env: base 1 + if + two `||` = 4.
    write_secret_file: base 1 + if = 2. activate_claude_oauth_access_token: base 1
    + if(33) + ||(39) + ||(40) + if(43) + if(49) + ||(49) + if(56) = 8: the
    `|| true` at 39 and 40 sits inside `"$( ... )"` and runs all the same. Lines
    39-40 also carry `<<<` herestrings: read as heredocs they would swallow the
    rest of the file."""
    assert [(f.name, f.cyclomatic_complexity)
            for f in _real("ci-hydrate-live-auth.sh")] == [
        ("append_profile_env", 4), ("write_secret_file", 2),
        ("activate_claude_oauth_access_token", 8)]


@needs_consumer_repo
def test_functions_defined_inside_a_heredoc_body_are_not_reported():
    """test-live-codex-harness-docker.sh defines six `name() {` headers. Three sit
    inside the `read -r -d '' LIVE_TEST_CMD <<'EOF'` body that runs from line 202
    to 339: they are the text of a command sent to a container, not code in this
    file."""
    assert [f.name for f in _real("test-live-codex-harness-docker.sh")] == [
        "openclaw_live_codex_harness_is_ci",
        "openclaw_live_codex_harness_append_build_extension",
        "cleanup_temp_dirs"]
