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


def test_a_command_substitution_inside_double_quotes_is_one_token():
    """lizard's shared string rule ends at the first inner quote, and every quote
    after it pairs off by one until a brace lands inside a string; on the consumer
    repo's install.sh that hid 18 of 153 functions. The whole run has to arrive as
    one token instead."""
    tokens = list(ShellReader.generate_tokens(NESTED_QUOTES))

    assert NESTED_QUOTES.split("=", 1)[1].rstrip("\n") in tokens


def test_a_plain_double_quoted_string_is_still_one_token():
    """The same alternative handles the ordinary case, or it would be a regression
    dressed as a fix."""
    assert '"hello world"' in list(ShellReader.generate_tokens('echo "hello world"\n'))


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
    + if(33) + if(43) + if(49) + ||(49) + if(56) = 6, and the `|| true` at 39-40
    is inside `"$( ... )"`, which counts nothing. Lines 39-40 also carry `<<<`
    herestrings: read as heredocs they would swallow the rest of the file."""
    assert [(f.name, f.cyclomatic_complexity)
            for f in _real("ci-hydrate-live-auth.sh")] == [
        ("append_profile_env", 4), ("write_secret_file", 2),
        ("activate_claude_oauth_access_token", 6)]


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
