"""A shell (sh/bash) reader for lizard, which ships none.

WHAT IS REPORTED
    Shell functions, in both spellings: `name() { ... }` and `function name { ... }`
    (`function name() { ... }` too). Top-level script code is NOT reported, exactly
    like Python module-level code in lizard: statements outside any function belong
    to lizard's `*global*` pseudo-function, which never reaches the function list.
    A script that is one long top-level sequence therefore reports nothing, and
    that is the intended answer, not a parse failure.

CCN CONVENTION
    Conditions counted: `if`, `elif`, `while`, `until`, `for`, `&&`, `||`, and
    `;;`. Pipes (`|`) are data flow, not branches, and are not counted.

    `case` is counted per arm, and the arm is its `;;` terminator rather than the
    `case` keyword, because the tokenizer makes `;;` the reliable half of that
    choice: this reader adds `;;` to lizard's shared token pattern, so an arm
    terminator arrives as one token and two adjacent semicolons have no other
    meaning in shell. Counting arms by their `)` would have to tell a pattern's
    `)` from a subshell's; counting the `case` keyword once is reliable too, but
    scores a twelve-arm dispatcher the same as a one-arm one.

    Bias of the per-arm choice, both directions:
      - POSIX lets the last arm before `esac` drop its `;;`. Written that way an
        N-arm case counts N-1.
      - bash's `;&` fallthrough terminator is not counted (`;;&` is, because it
        tokenizes as `;;` then `&`).
      - the `case` keyword itself adds nothing, so a one-arm case costs the same 1
        as an `if`.

HEREDOCS
    A heredoc body is data, so it is blanked out of the source before tokenizing:
    nothing in a body can contribute a condition, a function, or an unbalanced
    brace. Line count is preserved exactly and content is not, so every line number
    after a heredoc is right and a body counts 0 NLOC.

    This happens on the source rather than on the token stream because a body's own
    punctuation forms tokens that outlive it. On the consumer repo's clawlog.sh an
    apostrophe in help text ("Apple's privacy redaction") opened a string token that
    ran 74 lines to the next quote, swallowing the `EOF` terminator with it, and the
    rest of the file went with the body. Rewriting the source cannot be fooled that
    way, and it materializes nothing in lizard's extension chain: the token stream
    stays a generator, which is what crapkit's two-chain analyze.py depends on (see
    tests/unit/test_cognitive_reader_chain.py). This reader works with the cognitive
    extension at index 0.

    `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"` and `<<\\EOF` open a body; `<<<`
    (herestring) does not; a `<<` inside `$(( ))` reads as a bit shift; and an
    opener whose terminator never appears is ignored, so a misread `<<` costs
    nothing instead of blanking the rest of the file.

TOKENIZER REPAIRS
    lizard's shared token pattern is the C family's, and three of its rules read
    ordinary shell as something else. Each repair below was found by running this
    reader over the consumer repo's 97 scripts, which hold 475 function headers and
    report 462 (the rest are defined inside heredoc bodies). Each is pinned by a
    test naming the script it came from.
      - `/*` opens a C block comment and is shell's absolute-path glob. It cannot
        be preempted, because that alternative sits ahead of the one place a reader
        may extend the pattern, so the source gets a space between the two
        characters (install-cli.sh: 55 of 59 functions hidden).
      - `\\"` outside a string is an escaped literal quote in shell, not a string
        opener. An added `\\x` token spends it (test-live-acp-bind-docker.sh).
      - a double-quoted run holding a command substitution that holds quotes ends
        at the wrong quote, and every quote after it pairs off by one (install.sh:
        18 of 153 functions hidden).
    `//` gets an added token for the same reason as `\\x`: it is the `//` of a URL,
    not a C++ line comment.

KNOWN LIMITS
    - Conditions inside `"$( ... )"` are invisible: the whole double-quoted run is
      one string token. `x="$(cmd || true)"` counts 0.
    - A function defined inside another function's body is not reported; its braces
      are counted, so the outer function still closes on the right `}`.
    - A name containing `-` or `.` reaches the reader split into several tokens, so
      `do-thing() {` is reported under the name `thing`. It is still one function
      with the right span and ccn.
    - Cognitive complexity for shell is computed by crapkit's language-agnostic
      extension, whose C-family rule counts `do` as a structure. A shell loop
      therefore scores its keyword plus its `do`, and nesting stays flat because
      shell blocks close with `fi`/`done`/`esac` rather than `}`.

REGISTRATION
    lizard resolves a filename through `lizard_languages.get_reader_for`, which
    walks the hard-coded list `lizard_languages.languages()` and has no plugin hook
    (`CodeReader.extra_subclasses` exists in 1.24.0 and nothing reads it).
    `register()` wraps that function so the list gains this reader; importing this
    module runs it once. Every process that analyzes shell has to import it, worker
    processes included.

    Skipping it does not fail loudly. lizard falls back to CLikeReader
    (`(get_reader_for(filename) or CLikeReader)`), which accepts `f() { }` because
    it looks like C, so a wrong answer comes back shaped like a right one: `elif`
    and `until` stop counting and `function name { }` disappears.
"""
from __future__ import annotations

import re

import lizard_languages
from lizard_languages.code_reader import CodeReader, CodeStateMachine
from lizard_languages.script_language import ScriptLanguageMixIn

# A double-quoted run can hold a command substitution, and that substitution can
# hold quotes of its own: `v="$(node -e 'require("fs")' "$f")"`. lizard's shared
# rule ends the string at the first inner quote, and every quote after it pairs off
# by one until some brace lands inside a string. This alternative fires at the same
# '"' and wins, because a reader's additions are tried ahead of it. It allows three
# levels of parens inside the substitution; deeper, or unbalanced inside its own
# quotes, and it simply does not match, which leaves lizard's rule as it was.
_COMMAND_SUB = r"\$\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)"
_DQ_STRING = r'"(?:\\.|' + _COMMAND_SUB + r'|[^"\\])*"'

# Extra alternatives for lizard's shared token pattern. Order matters only among
# alternatives that can start at the same character.
#   "..."   the command-substitution-aware string above.
#   ${...}  one nesting level deep, so the '#' of ${PATH#/usr} never opens a
#           comment and the '}' never leaves the brace counter unbalanced.
#   $#      and friends, for the same reason: '$#' must not read as a comment.
#   ;;      one token, so a case arm is countable.
#   //      the '//' of a URL or of ${x//a/b}, ahead of lizard's C++ line comment.
#   \x      an unquoted backslash escapes the next character in shell, so '\"' is
#           a literal quote. Left to open a string it runs to the next real quote,
#           taking whatever braces lie between with it.
_TOKEN_ADDITION = (
    "|" + _DQ_STRING +
    r"|\$\{(?:[^{}]|\{[^}]*\})*\}"
    r"|\$[#?*@!$0-9-]"
    r"|;;"
    r"|//"
    r"|\\."
)

# `<<` or `<<-`, then an optionally quoted delimiter word. '<<<' is excluded from
# both sides so a herestring never reads as a heredoc.
_HEREDOC = re.compile(
    r"(?<!<)<<(?!<)(-?)\s*(?:(['\"])([A-Za-z_]\w*)\2|\\?([A-Za-z_]\w*))")

_NAME = re.compile(r"[A-Za-z_]\w*")

# Words that can never name a function, so that `if (cmd); then` and
# `case $x in (a)` cannot look like one.
_KEYWORDS = frozenset({
    "if", "then", "elif", "else", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "in", "select", "function", "time", "coproc", "return",
})


def _is_name(token) -> bool:
    return bool(token) and token not in _KEYWORDS and bool(_NAME.fullmatch(token))


# --- heredoc bodies, removed from the source ----------------------------------

def _quoted(line: str, position: int) -> bool:
    """True when an odd number of quotes precedes this '<<' on its line, which
    puts it inside a string: `echo "pipe it <<EOF"` opens nothing."""
    head = line[:position]
    return head.count('"') % 2 == 1 or head.count("'") % 2 == 1


def _shifted(line: str, position: int) -> bool:
    """True when this '<<' sits inside arithmetic, where it is a bit shift:
    `$(( 1 << bits ))` puts a bare word exactly where a delimiter would go."""
    return line.count("((", 0, position) > line.count("))", 0, position)


def _openers(line: str) -> list:
    """(delimiter, dashed) for every heredoc this line opens."""
    return [(match.group(3) or match.group(4), bool(match.group(1)))
            for match in _HEREDOC.finditer(line)
            if not _quoted(line, match.start()) and not _shifted(line, match.start())]


def _terminates(line: str, opener) -> bool:
    delimiter, dashed = opener
    text = line.rstrip("\r\n")
    return (text.lstrip("\t") if dashed else text) == delimiter


def _terminated(opener, lines: list, index: int) -> bool:
    """Whether a terminator for this opener exists further down the file.

    The safety valve on every heuristic above: an opener nothing closes is not an
    opener, so a `<<` this module misreads costs nothing instead of blanking the
    file from that line to its end.
    """
    return any(_terminates(line, opener) for line in lines[index + 1:])


class _HeredocStripper:
    """Blanks every heredoc body line, keeping the file's line count exact."""

    def __init__(self) -> None:
        self.pending: list = []   # delimiters opened on one line, bodies not started
        self.active = None        # the delimiter whose body we are inside

    def strip(self, source: str) -> str:
        lines = source.splitlines(keepends=True)
        return "".join(self._line(line, lines, index)
                       for index, line in enumerate(lines))

    def _line(self, line: str, lines: list, index: int) -> str:
        if self.active is not None:
            return self._body_line(line)
        # The opener line is code and stays whole; the body starts on the next one.
        self.pending = [o for o in _openers(line) if _terminated(o, lines, index)]
        self._next_body()
        return line

    def _body_line(self, line: str) -> str:
        if _terminates(line, self.active):
            self._next_body()
        return "\n" if line.endswith("\n") else ""

    def _next_body(self) -> None:
        """One line can open several bodies (`cat <<A <<B`); they run in order."""
        self.active = self.pending.pop(0) if self.pending else None


def _defuse_block_comments(source: str) -> str:
    """Pull apart every `/*`, which is a glob in shell and a comment opener in
    lizard's shared token pattern.

    That alternative sits ahead of the one place a reader is allowed to extend the
    pattern, so it cannot be preempted the way `//` is; a `case "$1" in /*)` runs
    to the next `*/` anywhere in the file, braces and all. On the consumer repo's
    install-cli.sh it hid 55 of 59 functions. A space between the two characters
    ends it at the source, before any alternative can fire, and costs nothing this
    reader measures: it adds no line and no token.
    """
    return source.replace("/*", "/ *")


# --- function detection --------------------------------------------------------

class ShellStates(CodeStateMachine):
    """Function detection over the meaningful tokens only.

    Whitespace is dropped here rather than relied upon to have been dropped
    upstream: `last_token` is what decides whether a '(' opens a function, and
    lizard's whitespace-stripping `preprocessing` is an extension a caller can
    reorder or omit (crapkit's analyze.py builds two different chains). Dropping
    newlines too is what lets a `foo()` header sit a line above its `{`.
    """

    def __init__(self, context):
        super().__init__(context)
        self._name = ""
        self._start = 0
        self._opener = "{"
        self._closer = "}"
        self._depth = 0

    def __call__(self, token, reader=None):
        if token.isspace():
            return None
        return super().__call__(token, reader)

    def _state_global(self, token):
        if token == "function":
            self._state = self._function_keyword
        elif token == "(" and _is_name(self.last_token):
            self._name = self.last_token
            self._start = self.context.current_line
            self._state = self._expect_close_paren

    def _function_keyword(self, token):
        if not _is_name(token):
            return self.next(self._state_global, token)
        self._name = token
        self._start = self.context.current_line
        self._state = self._after_function_name

    def _after_function_name(self, token):
        if token == "(":
            self._state = self._expect_close_paren
        else:
            self.next(self._expect_body, token)

    def _expect_close_paren(self, token):
        """Only an empty '()' is a function header; anything else was a subshell
        or a command substitution, and the tokens go back to the global state."""
        if token == ")":
            self._state = self._expect_body
        else:
            self.next(self._state_global, token)

    def _expect_body(self, token):
        if token in ("{", "("):
            self._begin(token)
        else:
            self.next(self._state_global, token)

    def _begin(self, opener):
        self.context.restart_new_function(self._name)
        self.context.add_to_long_function_name("()")
        # The header can sit a line above its brace (`foo()\n{`); the function
        # starts where its name is, not where its body opens.
        self.context.current_function.start_line = self._start
        self._opener = opener
        self._closer = ")" if opener == "(" else "}"
        self._depth = 1
        self._state = self._body

    def _body(self, token):
        if token == self._opener:
            self._depth += 1
        elif token == self._closer:
            self._depth -= 1
            if not self._depth:
                self._end()

    def _end(self):
        self.context.end_of_function()
        self._state = self._state_global

    def statemachine_before_return(self):
        """A file ending inside a function still reports it. A dropped function is
        invisible; one with a wrong end line is a number someone can see."""
        if self._state == self._body:
            self._end()


class ShellReader(CodeReader, ScriptLanguageMixIn):
    """See the module docstring for the counting convention and its bias."""

    ext = ["sh", "bash"]
    language_names = ["shell"]

    _control_flow_keywords = {"if", "elif", "for", "while", "until", ";;"}
    _logical_operators = {"&&", "||"}
    _case_keywords = set()      # arms are counted as ';;', see the module docstring
    _ternary_operators = set()  # shell has no '?:'

    # What lizard's ND extension treats as a nesting structure; its default set is
    # the C family's and mentions neither `elif` nor `until`.
    loops = {"if", "elif", "for", "while", "until", "&&", "||"}

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [ShellStates(context)]

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        """lizard's shared tokenizer, minus heredoc bodies, plus shell's tokens.

        ScriptLanguageMixIn supplies the '#' comment rule (PythonReader uses the
        same one), so comment handling is not written here. Both repairs are made
        to the source, so the token stage stays the single generator lizard built:
        it yields as it reads, and nothing ahead of it in the extension chain is
        starved.
        """
        source = _defuse_block_comments(_HeredocStripper().strip(source_code))
        return ScriptLanguageMixIn.generate_common_tokens(
            source, _TOKEN_ADDITION + addition, token_class)


# Captured before register() wraps it, so a test can ask what lizard shipped.
_stock_languages = lizard_languages.languages

_MARK = "_crapkit_shell_reader"


def register():
    """Make `lizard_languages.get_reader_for` resolve .sh and .bash to ShellReader.

    Idempotent: a second call is a no-op, and wrapping composes with any other
    reader registered the same way, because each wrapper calls the one it replaced.
    Appending rather than prepending leaves every stock reader's claim intact.
    """
    if getattr(lizard_languages.languages, _MARK, False):
        return ShellReader
    inner = lizard_languages.languages

    def languages():
        return inner() + [ShellReader]

    setattr(languages, _MARK, True)
    lizard_languages.languages = languages
    return ShellReader


register()
