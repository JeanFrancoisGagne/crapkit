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

    Inside arithmetic, `(( ))` and `$(( ))`, bash reads C: the `?` of `a ? b : c`
    counts as C's does, and the `;;` of `for ((;;))` is no case arm. Outside it
    `?` is a glob character and counts nothing. See `_Arithmetic`.

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
    A `;;` with no case open, as in `for ((;;))`, ends no arm and counts nothing.

RESERVED WORDS
    Shell reads `if`, `done` and the rest as reserved only first in a command,
    straight after another reserved word, as the `do` after a for's name and as
    the `esac` where a case pattern would start, and only when a blank or an
    operator ends the word (POSIX XCU 2.4). Anywhere else each is an ordinary
    word, and it reaches the counters as text (_CommandWords): `echo done` closes
    no loop, the `for` of `git for-each-ref` and the `select` of `xcode-select`
    open no block, `done=1` assigns a variable, and a case pattern such as
    `--exit-if-exists)` or `(done|fi)` is a pattern.

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
    (herestring) does not; a `<<` inside `$(( ))` reads as a bit shift; a `<<`
    inside a string or a comment is text. What a `<<` sits in is read across
    lines (_Context): each code line starts inside whatever the lines above left
    open, so `v="$(node - "$f" <<'JS'` opens a body, a `<<'JS'` on the line that
    closes a multi-line `X="$(...)"` opens one too, and a `<<` in the second line
    of a string or of a multi-line single-quoted program opens none. An opener
    whose terminator never appears is ignored, so a misread `<<` costs nothing
    instead of blanking the rest of the file.

    A line ends where bash ends it, at LF (the source arrives with CRLF and a lone
    CR already read as LF), never at a form feed or another character
    `str.splitlines` also splits at. `note<FF>EOF` is one body line that does not
    end the body, and code after a form feed on the opener line is still code.

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

SUBSTITUTIONS INSIDE STRINGS
    `x="$(cmd || true)"` runs `cmd || true`: the quotes keep the output one word,
    they do not make the command text. The string rule above matches the whole
    run, so its inner quotes pair with each other, and then every `$( )`, `$(( ))`
    and backtick substitution inside a double-quoted token or a `${...}` expansion
    is tokenized again as code. Its `&&`, `||`, `if` and `;;` count the way they
    count written bare, at any depth: `"$(a "$(b || c)")"` reaches the `||`. The
    text around a substitution stays one string token, so the `}` that ends
    `${v:-$(cmd)}` closes nothing. A `$(` after a backslash, or inside single
    quotes, is text and stays in its string. A case statement inside a
    substitution is matched whole, from `case WORD in PATTERN)` to its `esac`,
    because each pattern ends in a bare `)` that does not close the substitution:
    `"$(case $os in Linux) echo l;; esac)"` counts its arm and opens and closes
    one level.

KNOWN LIMITS
    - The string rule reads eight levels of parens inside a substitution, the
      `$(` included, and a case in it only when its subject is one word and its
      first pattern follows `in` with no comment between. Past either, the
      string ends at its first inner quote, as lizard's own rule ends it, and
      the command counts nothing.
    - A substitution in a heredoc body runs when the delimiter is unquoted
      (`<<EOF`, not `<<'EOF'`), and it counts nothing: the whole body is blanked.
    - What a line leaves open carries to the next, so a quote the heredoc reader
      misreads stays open: a heredoc after it reads as text and its body counts
      as shell. Only a script shell itself rejects, such as an unpaired `'` in a
      bare word, leaves one open. A case opens a context only when `case WORD
      in` sits on one line.
    - A function defined inside another function's body is not reported; its braces
      are counted, so the outer function still closes on the right `}`.
    - A name containing `-` or `.` reaches the reader split into several tokens, so
      `do-thing() {` is reported under the name `thing`. It is still one function
      with the right span and ccn.
    - Cognitive complexity for shell is computed by crapkit's language-agnostic
      extension, which reads a block by its words rather than its braces: `if`,
      `case` and the loop keywords open a nesting level, `fi`/`done`/`esac` close
      it, and `do`/`then`/`in` introduce a body already charged and cost nothing.
    - Arithmetic is read inside `(( ))` and `$(( ))` only. A `?:` in `let "..."`,
      in an array subscript (`a[i ? 1 : 0]=x`) or in the old `$[ ]` form counts
      nothing.

NESTING DEPTH
    A shell row's `nesting` is the deepest that same extension's block stack
    gets, not lizard's ND column (analyze._nesting_depth). ND closes a level on a
    `}` or at a `;`, and shell closes a block with a word, so every block leaked
    a level: seven ifs side by side read 6, four nested read 3, and a `case` read
    0. Read off the stack, the seven read 1, the four read 4, and a `case` opens
    one level its arms share, the way Sonar's switch does. `&&` and `||` open none.

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

from crapkit.sourcelines import source_lines

# A double-quoted run can hold a command substitution, and that substitution can
# hold quotes of its own: `v="$(node -e 'require("fs")' "$f")"`. lizard's shared
# rule ends the string at the first inner quote, and every quote after it pairs off
# by one until some brace lands inside a string. This alternative fires at the same
# '"' and wins, because a reader's additions are tried ahead of it. It allows
# _PAREN_LEVELS levels of parens inside the substitution; deeper, or unbalanced
# inside its own quotes, and it simply does not match, which leaves lizard's rule
# as it was.
#
# A case statement inside the substitution is taken whole, from `case WORD in
# PATTERN)` to its first `esac`, because each pattern ends in a bare `)`. Read as
# the substitution's close, it cut `"$(case $os in Linux) echo l;; esac)"` at
# `Linux)`: `case` reached the counters and `esac` stayed in the string.
#
# Every loop is possessive (`*+`, `++`): it never gives back what it matched. A
# string with no closing quote after it made the backtracking version try each
# `$( )` both as a substitution and as text, twice the time per substitution.
_PAREN_LEVELS = 8
_CASE_START = (r"\bcase\s++(?:\"[^\"]*+\"|[^\s\"])++\s++in\s++\(?+"
               r"(?:\"[^\"]*+\"|'[^']*+'|[^\s\"'()])++\)")
_CASE_BLOCK = _CASE_START + r"(?:(?!\besac\b)[\s\S])*+\besac\b"


def _parens(levels: int) -> str:
    """What may sit between a `(` and its `)`, LEVELS levels of parens deep."""
    content = _run("")
    for _ in range(levels - 1):
        content = _run(r"|\(" + content + r"\)")
    return content


def _run(nested: str) -> str:
    return (r"(?:" + _CASE_BLOCK + r"|(?!" + _CASE_START + r")[^()]" + nested
            + r")*+")


_COMMAND_SUB = r"\$\(" + _parens(_PAREN_LEVELS) + r"\)"
_DQ_STRING = r'"(?:\\.|' + _COMMAND_SUB + r'|[^"\\])*+"'

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

# A substitution inside a token lizard keeps as text: a double-quoted string or a
# ${...} expansion. The escape comes first, so `\$(` and `\`` stay text, as the
# string rule above spent them.
_BACKTICK_SUB = r"`(?:\\.|[^`\\])*`"
_HOLE = re.compile(r"\\.|(" + _COMMAND_SUB + "|" + _BACKTICK_SUB + ")", re.S)

# `<<` or `<<-`, then an optionally quoted delimiter word. '<<<' is excluded from
# both sides so a herestring never reads as a heredoc.
_HEREDOC = re.compile(
    r"(?<!<)<<(?!<)(-?)\s*(?:(['\"])([A-Za-z_]\w*)\2|\\?([A-Za-z_]\w*))")

# What each context of a line reads next, for _Context. `code` serves top-level
# code and everything that holds code: `$( )`, `( )`, backticks and a case. A
# lexeme is matched only where its context can hold it: a `'` opens a string in
# code and is a letter inside "...", and a `)` closes a `$(` but not a case
# pattern. _AT_WORD is the start of a word, where `#` opens a comment and `case`
# is a keyword.
_CODE = "code"
_AT_WORD = r"(?<![^\s;&|()])"
_INTO = r"|(?P<arith>\$\(\()|(?P<sub>\$\()|(?P<param>\$\{)|(?P<backtick>`)"
_LEXEMES = {
    _CODE: re.compile(
        r"(?P<skip>\\.|<<<)|(?P<comment>" + _AT_WORD + r"\#[^\n]*)|(?P<heredoc><<)"
        r"|(?P<ansi>\$')|(?P<single>')|(?P<double>\")"
        r"|(?P<arith_command>" + _AT_WORD + r"\(\()" + _INTO + r"|(?P<paren>\()|(?P<close>\))"
        r"|(?P<case>" + _AT_WORD + r"case\s+(?:\"[^\"]*\"|'[^']*'|[^\s\"'])+\s+in\b)"
        r"|(?P<esac>" + _AT_WORD + r"esac\b)", re.S),
    '"': re.compile(r"(?P<skip>\\.)|(?P<pop>\")" + _INTO, re.S),
    "'": re.compile(r"(?P<pop>')"),
    "$'": re.compile(r"(?P<skip>\\.)|(?P<pop>')", re.S),
    "${": re.compile(r"(?P<skip>\\.)|(?P<pop>\})|(?P<double>\")" + _INTO, re.S),
    "((": re.compile(r"(?P<pop>\)\))|(?P<aparen>\()" + _INTO),
    "a(": re.compile(r"(?P<pop>\))|(?P<aparen>\()" + _INTO),
}
# The context each opening lexeme enters. "a(" is a paren inside arithmetic.
_OPENS = {"ansi": "$'", "single": "'", "double": '"', "arith": "((",
          "arith_command": "((", "sub": "$(", "param": "${", "paren": "(",
          "aparen": "a(", "case": "case"}

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

class _Context:
    """What is open at the end of the code lines read so far, innermost last:
    quotes, `$( )`, `( )`, backticks, `${ }`, arithmetic and case statements.

    A `<<` opens a heredoc only in code, and a line can start inside something
    an earlier line opened:

        X="$(
          printf x
        )" node - "$p" <<'JS'

    Counted on the last line alone, three quotes precede its `<<` and it read
    as quoted. Carried from line 2, the `"` closes the string and the `<<` sits
    in code. The same stack puts `v="$(node - "$f" <<'JS'` in code, a `<<` in
    `$(( 1 << bits ))` in arithmetic, where it is a shift, and a case pattern's
    `)` in the case rather than at the close of its `$(`.
    """

    def __init__(self) -> None:
        self.stack: list = []
        self._actions = {"pop": self.stack.pop, "close": self._close,
                         "backtick": self._backtick, "esac": self._esac}

    def openers(self, line: str) -> list:
        """(delimiter, dashed) for every heredoc LINE opens in code, reading the
        line to its end so the next one starts where this one leaves off."""
        found: list = []
        match = self._next(line, 0)
        while match:
            match = self._next(line, self._read(line, match, found))
        return found

    def _next(self, line: str, position: int):
        top = self.stack[-1] if self.stack else _CODE
        return _LEXEMES.get(top, _LEXEMES[_CODE]).search(line, position)

    def _read(self, line: str, match, found: list) -> int:
        """Act on one lexeme; return where the next search starts."""
        if match.lastgroup == "heredoc":
            return _heredoc(line, match, found)
        opens = _OPENS.get(match.lastgroup)
        if opens:
            self.stack.append(opens)
        else:
            self._actions.get(match.lastgroup, _nothing)()
        return match.end()

    def _close(self) -> None:
        """A `)` closes a `$(` or a `(`. After a case pattern it closes nothing."""
        if self.stack and self.stack[-1] in ("$(", "("):
            self.stack.pop()

    def _backtick(self) -> None:
        if self.stack and self.stack[-1] == "`":
            self.stack.pop()
        else:
            self.stack.append("`")

    def _esac(self) -> None:
        if self.stack and self.stack[-1] == "case":
            self.stack.pop()


def _nothing() -> None:
    """A lexeme read only so the search moves past it: an escape, a comment."""


def _heredoc(line: str, match, found: list) -> int:
    """Record the heredoc whose `<<` MATCH found, and skip its delimiter word, so
    the quotes of `<<'JS'` open nothing."""
    opener = _HEREDOC.match(line, match.start())
    if not opener:
        return match.end()
    found.append((opener.group(3) or opener.group(4), bool(opener.group(1))))
    return opener.end()


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
        self.context = _Context()  # what the code lines so far leave open

    def strip(self, source: str) -> str:
        lines = source_lines(source, keepends=True)
        return "".join(self._line(line, lines, index)
                       for index, line in enumerate(lines))

    def _line(self, line: str, lines: list, index: int) -> str:
        if self.active is not None:
            return self._body_line(line)
        # The opener line is code and stays whole; the body starts on the next one.
        self.pending = [o for o in self.context.openers(line)
                        if _terminated(o, lines, index)]
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


# --- arithmetic, where `?` decides and `;;` is no case arm ---------------------

# What a parenthesis does to the depth of an open arithmetic expression.
_PARENS = {"(": 1, ")": -1}


class _Arithmetic:
    """Moves `?` and `;;` in and out of the reader's condition set as `(( ))`
    opens and closes.

    Inside `(( ))`, `$(( ))` and a C-style `for (( ))`, bash reads C's
    arithmetic: `a ? b : c` is the conditional operator, one decision as in C,
    and `for ((;;))` writes a loop's three empty clauses, not a case arm's end.
    Everywhere else `?` is a glob character that matches one character (`ls
    a?b`) and `;;` ends a case arm. lizard's condition counter and crapkit's
    cognitive pass both read the set when they reach a token, so the two
    columns count the same `?`.

    It reads raw tokens, whitespace included: the two parentheses of `((`
    touch, and `( (cmd) )`, with a space between them, is two subshells.
    """

    def __init__(self, conditions: set):
        self._conditions = conditions
        self._depth = 0        # parentheses open since `((`, its own two included
        self._previous = ""    # the raw token before this one

    def __call__(self, token: str) -> None:
        was_open = self._depth > 0
        self._depth = self._depth_after(token)
        self._previous = token
        if (self._depth > 0) != was_open:
            self._switch(self._depth > 0)

    def _depth_after(self, token: str) -> int:
        if self._depth:
            return self._depth + _PARENS.get(token, 0)
        return 2 if token == "(" and self._previous == "(" else 0

    def _switch(self, arithmetic: bool) -> None:
        counted, free = ("?", ";;") if arithmetic else (";;", "?")
        self._conditions.add(counted)
        self._conditions.discard(free)


# --- substitutions inside strings, read as code --------------------------------

def _tokens(source: str, addition: str, token_class):
    """lizard's shared tokenizer with shell's tokens, every substitution opened."""
    return _open_holes(ScriptLanguageMixIn.generate_common_tokens(
        source, _TOKEN_ADDITION + addition, token_class), addition, token_class)


def _open_holes(tokens, addition: str, token_class):
    for token in tokens:
        if _may_hold_code(token):
            yield from _hole_tokens(token, addition, token_class)
        else:
            yield token


def _may_hold_code(token: str) -> bool:
    """A double-quoted string or a ${...} expansion with a `$(` or a backtick in it."""
    return (token[:1] == '"' or token[:2] == "${") and ("$(" in token or "`" in token)


def _hole_tokens(token: str, addition: str, token_class):
    """TOKEN's substitutions as code, and the text around each as a string token.

    The text is quoted again so none of it reads as a word, a brace or a comment,
    and it keeps every newline it held, so lizard's line count sees each one.
    """
    start = 0
    for hole in _HOLE.finditer(token):
        if hole.group(1):
            yield '"' + token[start:hole.start()].strip('"') + '"'
            yield from _tokens(hole.group(1), addition, token_class)
            start = hole.end()
    yield '"' + token[start:].strip('"') + '"'


# --- reserved words, read only where shell reads them --------------------------
#
# Shell reads `if`, `done` and the rest as reserved only first in a command,
# straight after another reserved word, as the `do` after a for's name and as the
# `esac` where a case pattern would start, and only when a blank or an operator
# ends the word (POSIX XCU 2.4). Anywhere else each is an ordinary word. Every
# counter matches these words by their text, so one that stands anywhere else is
# handed on quoted, as text.

# Where the next word stands.
_COMMAND, _ARGUMENT, _PATTERN, _SUBJECT, _LOOP_NAME, _AFTER_NAME = range(6)

# The words a counter reads (ccn, the cognitive pass, ShellStates), and `time`,
# which a command follows. `in` stays as it is: nothing counts it, and
# ShellStates finds a case's `in` by it.
_RESERVED = frozenset({"if", "then", "elif", "else", "fi", "for", "select", "while",
                       "until", "do", "done", "case", "esac", "function", "time"})
# Where a reserved word leaves the next word; one not listed leaves a command.
_RESERVED_NEXT = {"case": _SUBJECT, "for": _LOOP_NAME, "select": _LOOP_NAME,
                  "function": _ARGUMENT}
# The two reserved words shell reads where no command starts.
_RESERVED_ELSEWHERE = {("do", _AFTER_NAME), ("esac", _PATTERN)}
# Where an operator leaves the next word. A redirection sign is followed by a file.
_OPERATOR_NEXT = {";": _COMMAND, "&&": _COMMAND, "||": _COMMAND, "|": _COMMAND,
                  "!": _COMMAND, "{": _COMMAND, "}": _ARGUMENT, "<": _ARGUMENT,
                  ">": _ARGUMENT}
# Where an ordinary word leaves the next: a loop's name is followed by `in` or
# `do`, and a case's subject runs to its `in`.
_WORD_NEXT = {_LOOP_NAME: _AFTER_NAME, _SUBJECT: _SUBJECT}
# After `in`, a case reads patterns and a loop reads its words.
_IN_NEXT = {_SUBJECT: _PATTERN, _AFTER_NAME: _ARGUMENT}


def _with_next(tokens):
    """Each token with the one after it, and "" after the last."""
    tokens = iter(tokens)
    current = next(tokens, None)
    for following in tokens:
        yield current, following
        current = following
    if current is not None:
        yield current, ""


def _blank(token: str) -> bool:
    """Whitespace, a comment, or a backslash that continues the line."""
    return token.isspace() or token[:1] == "#" or token[:2] in ("\\\n", "\\\r")


def _delimited(following: str) -> bool:
    """Whether the token after a word ends it. `done=1` and `do-thing` are one
    word each in shell, which lizard's tokenizer splits."""
    return not following or following.isspace() or following[0] in ";&|()<>"


class _CommandWords:
    """Reads the token stream the way shell finds where each command starts, and
    quotes each reserved word that stands anywhere else."""

    def __init__(self) -> None:
        self._at = _COMMAND
        self._closes: list = []  # where each open `(` leaves the word after its `)`
        self._cases = 0          # open cases, so `;;` in `for ((;;))` starts no pattern
        self._last = ""          # the last token that was not blank

    def read(self, tokens):
        for token, following in _with_next(tokens):
            yield self._word(token, following)

    def _word(self, token: str, following: str) -> str:
        if _blank(token):
            self._line_end(token)
            return token
        recognized = self._recognizes(token, following)
        text = self._is_text(token, recognized)
        self._at = self._next(token, recognized)
        self._last = token
        return '"' + token + '"' if text else token

    def _is_text(self, token: str, recognized: bool) -> bool:
        """A reserved word where shell reads none, and a `;;` with no case open, as
        in `for ((;;))`, where it ends no arm."""
        return (token in _RESERVED and not recognized) or (token == ";;" and not self._cases)

    def _line_end(self, token: str) -> None:
        """A newline ends a command, but not a case's subject or its patterns."""
        if "\n" in token and token.isspace() and self._at not in (_PATTERN, _SUBJECT):
            self._at = _COMMAND

    def _recognizes(self, token: str, following: str) -> bool:
        return (token in _RESERVED and _delimited(following)
                and (self._at == _COMMAND or (token, self._at) in _RESERVED_ELSEWHERE))

    def _next(self, token: str, recognized: bool):
        if self._at == _PATTERN:
            return self._in_pattern(token, recognized)
        if token in ("(", ")"):
            return self._paren(token)
        if recognized:
            return self._reserved(token)
        return self._plain(token)

    def _in_pattern(self, token: str, recognized: bool):
        """A pattern runs to its `)`, through any `(`, `|` or word; an `esac`
        in its place ends the case."""
        if recognized:
            return self._reserved(token)
        return _COMMAND if token == ")" else _PATTERN

    def _reserved(self, token: str):
        self._cases = max(0, self._cases + {"case": 1, "esac": -1}.get(token, 0))
        return _RESERVED_NEXT.get(token, _COMMAND)

    def _paren(self, token: str):
        """A `(` opens commands, or an array's words after `=`; its `)` returns to
        where a word in the `(`'s place would have left the next one."""
        if token == ")":
            return self._closes.pop() if self._closes else _ARGUMENT
        self._closes.append(_WORD_NEXT.get(self._at, _ARGUMENT))
        return _ARGUMENT if self._last in ("=", "+=") else _COMMAND

    def _plain(self, token: str):
        if token in _OPERATOR_NEXT:
            return _OPERATOR_NEXT[token]
        if token in ("&", ";;"):
            return self._separator(token)
        if token == "in":
            return _IN_NEXT.get(self._at, _ARGUMENT)
        return _WORD_NEXT.get(self._at, _ARGUMENT)

    def _separator(self, token: str):
        """`;;` ends a case arm, and so does `&` after `;` or `;;` (`;&`, `;;&`).
        After a redirection sign `&` names a descriptor (`2>&1`); anywhere else it
        ends a command."""
        if token == ";;" or self._last in (";", ";;"):
            return _PATTERN if self._cases else _COMMAND
        return self._at if self._last in ("<", ">") else _COMMAND


# --- function detection --------------------------------------------------------

class ShellStates(CodeStateMachine):
    """Function detection over the meaningful tokens only.

    Whitespace is dropped here rather than relied upon to have been dropped
    upstream: `last_token` is what decides whether a '(' opens a function, and
    lizard's whitespace-stripping `preprocessing` is an extension a caller can
    reorder or omit (crapkit's analyze.py builds two different chains). Dropping
    newlines too is what lets a `foo()` header sit a line above its `{`.

    A body that is a subshell, `f() ( ... )`, ends at its own `)`, and a case
    pattern's bare `)` inside it is skipped: read as the close, the first
    pattern of a consumer repo's 140-line function ended it 72 lines early.
    """

    def __init__(self, context):
        super().__init__(context)
        self._name = ""
        self._start = 0
        self._opener = "{"
        self._closer = "}"
        self._depth = 0
        self._cases: list = []     # the depth each open `case ... in` sits at
        self._subject_left = 0     # tokens a `case` may still wait for its `in`

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
        self._cases = []
        self._state = self._body

    def _body(self, token):
        if token == self._opener:
            self._depth += 1
        elif token == self._closer and not self._ends_pattern(token):
            self._close()
        else:
            self._read_case(token)

    def _close(self):
        self._depth -= 1
        if not self._depth:
            self._end()

    def _ends_pattern(self, token) -> bool:
        """A `)` that ends a case pattern, not a subshell: `a)` in `case $v in a)`.
        It matters only in a body that is itself a subshell, `f() ( ... )`."""
        return token == ")" and self._case_here()

    def _case_here(self) -> bool:
        return bool(self._cases) and self._cases[-1] == self._depth

    def _read_case(self, token):
        """A case opens at its `in`, when at most two tokens sit between it and
        `case` (`$v` is two: `$` and `v`), and closes at its `esac`. A `case` word
        no `in` follows, as in `echo case`, opens nothing."""
        if token == "in" and self._subject_left > 0:
            self._cases.append(self._depth)
        elif token == "esac" and self._case_here():
            self._cases.pop()
        self._count_subject(token)

    def _count_subject(self, token):
        self._subject_left = 3 if token == "case" else self._subject_left - 1

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
    _ternary_operators = set()  # '?' decides only inside (( )), see _Arithmetic

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [ShellStates(context)]
        self._arithmetic = _Arithmetic(self.conditions)

    def preprocess(self, tokens):
        """lizard's whitespace filter, which calls this in place of its own, with
        every raw token shown to `_Arithmetic` first. Every counter behind this
        stage reads the condition set after the tokens before its own have been
        seen here."""
        for token in tokens:
            self._arithmetic(token)
            if not token.isspace() or token == "\n":
                yield token

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        """lizard's shared tokenizer, minus heredoc bodies, plus shell's tokens,
        with the command inside a quoted substitution read as code.

        ScriptLanguageMixIn supplies the '#' comment rule (PythonReader uses the
        same one), so comment handling is not written here. Both repairs are made
        to the source, and the substitutions are opened by a generator over
        lizard's, so the token stage still yields as it reads and nothing ahead
        of it in the extension chain is starved.
        """
        source = _defuse_block_comments(_HeredocStripper().strip(source_code))
        return _CommandWords().read(_tokens(source, addition, token_class))


# Captured before register() wraps it, so a test can ask what lizard shipped.
_stock_languages = lizard_languages.languages


def register():
    """Make `lizard_languages.get_reader_for` resolve .sh and .bash to ShellReader.

    Idempotent: a second call is a no-op, and wrapping composes with any other
    reader registered the same way, because each wrapper calls the one it replaced.
    Appending rather than prepending leaves every stock reader's claim intact.

    The guard asks the list whether it already carries this reader rather than
    stamping the function it installed. A stamp only answers for the OUTERMOST
    wrapper: with the PowerShell reader registered after this one, `languages` is
    that module's function, this module's stamp is not on it, and a second call
    here appends ShellReader a second time. Membership is the invariant the guard
    was reaching for anyway, it composes at any depth and in any order, and it
    costs one list build of 30 classes per call.
    """
    if ShellReader in lizard_languages.languages():
        return ShellReader
    inner = lizard_languages.languages

    def languages():
        return inner() + [ShellReader]

    lizard_languages.languages = languages
    return ShellReader


register()
