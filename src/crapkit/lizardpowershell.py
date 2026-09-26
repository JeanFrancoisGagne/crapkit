"""A PowerShell (.ps1/.psm1) reader for lizard, which ships none.

WHAT IS REPORTED
    Named function-shaped declarations, in all four spellings PowerShell has:
    `function Name { }`, `filter Name { }`, `workflow Name { }` and
    `configuration Name { }`. A name keeps its scope and its dots:
    `function script:Get-Thing` reports `script:Get-Thing`, and
    `function Get.Thing` reports `Get.Thing`, as PowerShell's parser names
    them. A declaring word declares only where it starts a statement and a
    name follows it, so `dotnet build --configuration $c`, `$o.filter` and
    `@{ filter = '*.txt' }` open nothing.

    The parameters are the header list's (`function Name ($a, $b) { }`) plus
    those of the `param(...)` block that opens the body, the usual shape of an
    advanced function: one per entry, whatever attributes, type and default it
    carries. Only the header list shows in the long name. The long name is the
    ratchet key, and a key that grew the block's parameters would orphan every
    mark recorded before this reader read the block.

    A class or an enum is skipped whole. Its methods get no row, and the
    decisions in them count toward no function, not the function whose body
    declares the class: a method is a function of its own (about_Classes).

    Top-level script code is NOT reported. Statements outside any declaration
    belong to lizard's `*global*` pseudo-function, exactly like Python module
    level and exactly like the shell reader beside this one, so a 189-line
    watchdog script whose only declaration is one nested helper reports one
    function. That is the answer, not a parse failure.

    An anonymous script block assigned to a variable (`$run = { ... }`) is not
    reported either: it has no name to key a ratchet row on.

CCN CONVENTION
    Conditions counted: `if`, `elseif`, `for`, `foreach`, `while`, `until`,
    `catch`, `trap`, `-and`, `-or`, `-xor`, `?`, PowerShell 7's `&&`, `||`,
    `??` and `??=`, and one point per `switch` arm. `else` costs nothing, as
    everywhere else in lizard. `&&` and `||` between pipelines run the right
    pipeline on the left one's outcome, and `??` evaluates its right side only
    for a null left one, so each is one decision like `-and`.

    `switch` counts per arm, not once for the block. PowerShell writes arms as
    bare patterns with no `case` keyword:

        switch ($x) {
            1 { ... }
            'a' { ... }
            { $_ -gt 5 } { ... }
            default { ... }
        }

    so an arm is a pattern and the block that opens directly inside the switch
    body after it, one point per arm whatever the pattern is, a script block
    included, and the `default` arm is free. That mirrors lizard's own C `switch`/`case` handling
    and both readers already in this package: the shell reader counts a `case`
    arm at its `;;` and the Rust reader counts a `match` arm at its `=>`, each
    for the same reason. Counting the keyword once instead would score a
    twelve-arm dispatcher the same 2 as a one-arm one. Measured on the
    hand-counted probe, a six-arm switch with a default scores 7, the same as
    the six-branch if/elseif chain doing the same work.

    The arms cost the same in `ccn_mod`. lizard's modified rule adds a point
    for a `switch` and takes one back per `case`, and a PowerShell arm has no
    `case`, so the reader sets `_modified_switch = False` and the opener gets
    no point: the two columns agree, as they do for a Rust `match`. Before,
    every switch read one higher in `ccn_mod` than in `ccn_std`.

    `switch` itself is deliberately NOT a control-flow keyword, and that is
    load-bearing beyond the double count: `[switch]$Force` is how PowerShell
    declares a boolean parameter, and 138 `param()` blocks in the 106-script
    corpus this reader was built against are full of them. Left in the keyword
    set, every advanced function paid a phantom point for its own signature.
    The arm counter opens a switch only where the word starts a statement, so
    neither `[switch]$Force` nor `git switch main` opens one, and the
    cognitive column skips a `switch` right after `[`.

    `?` stays a ternary operator, as lizard has it everywhere. In PowerShell 7
    it is the ternary; in every version it is also the `Where-Object` alias,
    so `Get-Process | ? { $_.CPU -gt 10 }` costs a point. Both spell a branch,
    so the point is not wrong, only differently earned.

KEYWORDS IN ANY CASE
    PowerShell keywords and operators are not case-sensitive
    (about_Language_Keywords), and every rule that reads one matches the
    lower-case spelling. So the tokenizer hands the rules `if` for `IF`,
    `function` for `Function` and `default` for `Default`, and `-or` for
    `-Or`, through `_Spelling`. A keyword word is respelled only where it
    starts a statement, because only there does PowerShell read it as a
    keyword: after a pipe `ForEach` is the ForEach-Object alias, and after a
    parameter `Default` is an argument (`Out-File -Encoding Default`).

    `-and` and `-or` come out as `&&` and `||`. lizard's ND column knows those
    two by spelling and adds one nesting level for the first of them in a
    condition. Read as loop words, each `-and` and `-or` added a level of its
    own, so `if ($a -and $b -or $c)` read nesting 3 where the same condition
    in TypeScript reads 2.

TOKENIZER
    The added alternatives are tried ahead of lizard's shared C-family rules,
    and they exist because eight PowerShell constructs read as something else
    there. Each is pinned by a test.
      - `<# ... #>` block comment. Left alone, `<` and `#` tokenize apart and
        the comment's keywords and braces all count.
      - `@" ... "@` and `@' ... '@` here-strings. A here-string body is data
        that routinely holds keywords and unbalanced braces; as one token it
        contributes neither.
      - `"..."` with the backtick as the escape, not the backslash. lizard's
        rule ends a string at a backslash-escaped quote and every quote after
        it pairs off by one. The rule also takes a `$( )` subexpression whole,
        so `"$(Join-Path "a" "b")"` is one string: ended at its second quote,
        `"$(Get-Item "x{")"` left a `{` in code and the function around it
        had no row.
      - `'...'` with `''` as the escape, which is PowerShell's, not `\'`.
      - `$var` and `$script:var` as one token, so a scope-qualified name does
        not split on its colon.
      - `Verb-Noun` as one token, so `Get-ChildItem` is a name rather than a
        subtraction, and `-and`/`-or`/`-Path` as one token, which is what makes
        the logical operators countable at all.
      - `??` and `??=` as one token each. Split, they read as two `?`
        ternaries and cost 2.
      - `:label` as one token, so a labeled loop or switch still starts its
        statement. The colon in `script:Name` has a word before it and stays
        apart.
    The `#` line-comment rule comes from ScriptLanguageMixIn, the same one
    PythonReader uses.

SUBEXPRESSIONS INSIDE STRINGS
    `"$($a -and $b)"` evaluates `$a -and $b`: the quotes make its value text,
    not the expression. Once the string rule above has matched a double-quoted
    string whole, every `$( )` in it is tokenized again as code, so its `-and`,
    `-or`, `if` and loops count the way they count written bare, one
    subexpression inside another included. The text around a subexpression
    stays one string token. A `` `$( `` is escaped and stays text, and a
    single-quoted string expands nothing.

KNOWN LIMITS
    - The string rule reads eight levels of parens inside a subexpression,
      the `$(` included. Past that, the string ends at its first inner quote,
      as it did before the rule took subexpressions whole, and the expression
      counts nothing: `"$(f (g (h (i (j (k (l (m ($a -and $b)))))))))"` reads
      ccn 1.
    - A `$( )` inside a `@" "@` here-string is evaluated and counts nothing:
      the whole here-string is one token.
    - A lower-case keyword that starts no statement still counts as a
      condition (`Write-Output if`), because lizard's condition counter reads
      the word alone. Only a capitalized one keeps its spelling there.
    - A here-string is recognized by `@"` and `"@` alone. PowerShell also
      requires the opener to end its line and the terminator to start one;
      this reader does not check either, so `@"` inside an expression opens a
      body that runs to the next `"@`.
    - A `param()` block's parameters count in `params` and are missing from a
      packet's `params` list, which crapkit reads off the long name.
    - A class's methods get no row, so their complexity is not gated.

REGISTRATION
    lizard resolves a filename through `lizard_languages.get_reader_for`, which
    walks the hard-coded list `lizard_languages.languages()` and has no plugin
    hook (`CodeReader.extra_subclasses` exists in 1.24.0 and nothing reads it).
    `register()` wraps that function so the list gains this reader; importing
    this module runs it once. Every process that analyzes PowerShell has to
    import it, worker processes included.

    Skipping it does not fail loudly. lizard answers
    `(get_reader_for(filename) or CLikeReader)`, and CLikeReader finds
    C-shaped functions in a .ps1 file: on the probe it reports the `if(...)`
    header of a one-line conditional as a function named `if`. A wrong answer
    comes back shaped like a right one.
"""
from __future__ import annotations

import re

import lizard_languages
from lizard_languages.code_reader import CodeReader, CodeStateMachine
from lizard_languages.golike import GoLikeStates
from lizard_languages.script_language import ScriptLanguageMixIn

# A `$( )` subexpression, up to _PAREN_LEVELS levels of parens deep. Its own
# quotes pair among themselves inside the string rule below; deeper, and the rule
# falls back to ending the string at the first inner quote, as it always did.
# Every loop is possessive (`*+`): with no closing quote left in the file, the
# backtracking version tried each `$( )` after the quote both as a
# subexpression and as text, twice the time per subexpression. No loop holds a
# negative lookahead or lookbehind: CPython 3.11.2 misreads one inside a
# possessive loop (lizardshell._loop spells such a loop so that it reads right).
_PAREN_LEVELS = 8


def _parens(levels: int) -> str:
    """What may sit between a `(` and its `)`, LEVELS levels of parens deep."""
    content = r"[^()]*+"
    for _ in range(levels - 1):
        content = r"(?:[^()]|\(" + content + r"\))*+"
    return content


_SUBEXPRESSION = r"\$\(" + _parens(_PAREN_LEVELS) + r"\)"

# Extra alternatives for lizard's shared token pattern, tried ahead of it.
# Order matters only among alternatives that can start at the same character:
# the here-strings precede the plain strings, and both precede `$var`.
_TOKEN_ADDITION = (
    r"|<\#[\s\S]*?\#>"          # <# block comment #>
    r"|@\"[\s\S]*?\"@"          # @" here-string "@
    r"|@'[\s\S]*?'@"            # @' here-string '@
    # "double" string, backtick is the escape, a $( ) subexpression taken whole
    r"|\"(?:`.|" + _SUBEXPRESSION + r"|[^\"`])*+\""
    r"|'(?:''|[^'])*'"          # 'single' string, '' is the escape
    r"|\$[\w:]+"                # $var, $script:var
    r"|(?<!\w):[A-Za-z_]\w*"    # :label, never the colon in script:Name
    r"|\?\?=?"                  # ?? and ??=, one operator each
    r"|[A-Za-z_]\w*(?:-\w+)+"   # Verb-Noun, one token
    r"|-\w+"                    # -and, -or, -eq, -Path
)

# A subexpression inside a double-quoted token. The escape comes first, so a
# `` `$( `` stays text, as the string rule above spent it.
_HOLE = re.compile(r"`.|(" + _SUBEXPRESSION + ")", re.S)

# PowerShell keywords are not case-sensitive (about_Language_Keywords), and
# every rule that reads one, here and in lizard, matches the lower-case
# spelling. These are the keywords some rule reads.
_KEYWORDS = frozenset({
    "if", "elseif", "else", "for", "foreach", "while", "until", "do", "switch",
    "default", "catch", "trap", "try", "finally",
    "function", "filter", "workflow", "configuration", "class", "enum"})

# The logical operators, as the rules read them. lizard's ND column knows `&&`
# and `||` by spelling and adds one level for the first of them in a
# condition; under their own spelling each `-and` and `-or` added a level, so
# `if ($a -and $b -or $c)` read nesting 3 where `if (a && b || c)` reads 2.
# Both short-circuit, as PowerShell 7's pipeline chains `&&` and `||` do, so
# the cognitive and cyclomatic rules read the two spellings alike too.
_OPERATORS = {"-and": "&&", "-or": "||", "-xor": "-xor"}

# What a statement can follow on the same line: `(` opens `$(...)` and
# `@(...)`, an assignment takes a statement on its right (`$x = switch ...`),
# and `""` is the start of the file.
_STATEMENT_OPENERS = frozenset({"", ";", "{", "}", "(", "=", "+=", "-=", "*=", "/=",
                                "%=", "??="})

# What keeps the next line in the same statement: a backtick escapes the line
# break, and a pipe hands the next line to a command.
_CONTINUATIONS = frozenset({"`", "|"})

# A `:label` token, which a loop or a switch statement may follow.
_LABEL = re.compile(r":[A-Za-z_]")

_BLOCK_COMMENT = "<#"
_COMMENT_OPENERS = ("#", _BLOCK_COMMENT)


def _begins_statement(previous: str, new_line: bool) -> bool:
    """Whether the token after `previous` is a statement's first word.

    PowerShell reads a keyword only there. Anywhere else the same word is an
    argument, a member or a hashtable key: `dotnet build --configuration`,
    `$o.filter`, `git switch main`, `$xs | ForEach { }`.
    """
    if previous in _CONTINUATIONS:
        return False
    return new_line or previous in _STATEMENT_OPENERS or _LABEL.match(previous) is not None


class _Spelling:
    """The spelling every rule reads: keywords and operators in lower case.

    Sees the raw token stream, whitespace and comments included, so a line
    break is a token here. A keyword keeps the case it is written in when it
    does not start a statement, because PowerShell does not read it as a
    keyword there either.
    """

    def __init__(self):
        self.previous = ""       # the last code token, as spelled
        self.new_line = False    # a line break since then

    def __call__(self, token: str) -> str:
        if token.isspace():
            self.new_line = self.new_line or "\n" in token
            return token
        spelled = self._spell(token)
        if not token.startswith(_COMMENT_OPENERS):
            self.previous, self.new_line = spelled, False
        return spelled

    def _spell(self, token: str) -> str:
        lower = token.lower()
        if lower in _OPERATORS:
            return _OPERATORS[lower]
        if lower in _KEYWORDS and _begins_statement(self.previous, self.new_line):
            return lower
        return token


def _spelled(tokens):
    spelling = _Spelling()
    return (spelling(token) for token in tokens)

# The keywords that declare something with a name and a brace body, and the
# state that reads the name. A class or an enum is read only to skip its body.
_DECLARES = {"function": "_function_name", "filter": "_function_name",
             "workflow": "_function_name", "configuration": "_function_name",
             "class": "_type_name", "enum": "_type_name"}

# What joins the parts of one function name: `script:Get-Thing`, `Get.Thing`.
_NAME_JOINERS = frozenset({":", "."})

# The wildcard arm of a switch, free exactly like C's `default:`.
_DEFAULT_ARM = "default"

# Any other pattern: a value, a string, a variable or a script block.
_ARM = "arm"

# What ends a switch's flags and subject outside their parentheses: its body's
# `{`, or a `;` or `}` that ends a statement which opened no body.
_SUBJECT_ENDS = frozenset({"{", ";", "}"})
_PAREN_CHANGE = {"(": 1, ")": -1}
_BRACE_CHANGE = {"{": 1, "}": -1}

# What a bracket does to the depth a param() block is read at.
_DEPTH_CHANGE = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}


def _is_name(token: str) -> bool:
    """A word that can name a function or a type, as `=`, `$x` and `{` cannot."""
    return token[:1].isalnum() or token[:1] == "_"


class _ParamBlock:
    """The parameters a function's param(...) block declares.

    One per comma-separated entry: the entry's first variable at the block's
    own depth. An attribute, a type or a default sits inside brackets or after
    that variable, so `[Parameter(Mandatory = $true)][string]$Name =
    $env:USERNAME` is the one parameter `$Name`.
    """

    def __init__(self, function):
        self._function = function
        self._depth = 1        # inside the block's own parentheses
        self._named = False    # the current entry has its variable

    def read(self, token: str) -> bool:
        """Read one token; True once the block's closing parenthesis is read."""
        self._depth += _DEPTH_CHANGE.get(token, 0)
        if self._depth == 1:
            self._entry(token)
        return self._depth == 0

    def _entry(self, token: str) -> None:
        if token == ",":
            self._named = False
        elif token.startswith("$") and not self._named:
            self._function.full_parameters.append(token)
            self._named = True


class PowerShellStates(GoLikeStates):
    """Function detection: the Go machine, with PowerShell's differences.

    Go declares with one keyword, `func`, and PowerShell with four, so the
    global state matches the set, and only where the word starts a statement:
    `dotnet build --configuration`, `$o.filter` and `@{ filter = 1 }` declare
    nothing. A word that turns out to name nothing hands back the function it
    opened (`_abandon`), so the function around it keeps its row.

    A name may carry a scope (`script:Get-Thing`) or dots (`Get.Thing`); its
    parts arrive as separate tokens and are joined back. Go writes
    `func name(args) {` and always has the parameter list, while PowerShell
    usually writes `function Name {` and declares its parameters in a
    `param(...)` block that opens the body, which `_ParamBlock` reads. A class
    or an enum opens a function too, one that is never listed, so the
    decisions in its methods count toward no function. Everything else, the
    brace bookkeeping and nested declarations, is the inherited machine.
    """

    FUNC_KEYWORD = "function"

    def __init__(self, context):
        super().__init__(context)
        self._previous = ""      # the last token read, block comments aside
        self._line = 0           # the line it ended on
        self._statement = True   # the token being read starts a statement
        self._parameters = None  # the param(...) block being read

    def __call__(self, token, reader=None):
        if token.startswith(_BLOCK_COMMENT):
            return None
        self._statement = _begins_statement(self._previous, self.context.current_line != self._line)
        done = super().__call__(token, reader)
        self._previous, self._line = token, self.context.current_line
        return done

    def _state_global(self, token):
        if self._statement and token in _DECLARES:
            self.context.push_new_function("")
            self._state = getattr(self, _DECLARES[token])
        elif token == "{":
            self.sub_state(self.statemachine_clone())
        elif token == "}":
            self.statemachine_return()

    def _abandon(self, token):
        """The declaring word named nothing: take back the function it opened
        and read the token as the code it is."""
        self.context.current_function = self.context.stacked_functions.pop()
        self.next(self._state_global, token)

    def _function_name(self, token):
        if _is_name(token):
            self.context.add_to_function_name(token)
            self._state = self._expect_function_dec
        else:
            self._abandon(token)

    def _expect_function_dec(self, token):
        if token in _NAME_JOINERS:
            self.context.add_to_function_name(token)
            self._state = self._function_name
        elif token == "{":
            self.next(self._expect_function_impl, token)
        elif token == "(":
            self.next(self._function_dec, token)
        else:
            self._abandon(token)

    def _function_impl(self, _):
        body = self.statemachine_clone()
        body.next(body._body_start)
        self.sub_state(body, self._end_function)

    def _end_function(self):
        self._state = self._state_global
        self.context.end_of_function()

    def _body_start(self, token):
        """A function body's first statement, where its param() block sits,
        after any attribute such as `[CmdletBinding()]`."""
        if token == "[":
            self.next(self._attribute, token)
        elif token.lower() == "param":
            self._state = self._param_open
        else:
            self.next(self._state_global, token)

    @CodeStateMachine.read_inside_brackets_then("[]", "_body_start")
    def _attribute(self, _):
        pass

    def _param_open(self, token):
        if token == "(":
            self._parameters = _ParamBlock(self.context.current_function)
            self._state = self._param_block
        else:
            self.next(self._state_global, token)

    def _param_block(self, token):
        if self._parameters.read(token):
            self._state = self._state_global

    def _type_name(self, token):
        if _is_name(token):
            self._state = self._type_header
        else:
            self._abandon(token)

    def _type_header(self, token):
        """`: Base, IThing` up to the body, which is skipped whole."""
        if token == "{":
            self.sub_state(self.statemachine_clone(), self._drop_type)

    def _drop_type(self):
        """The type's body ended. The function its `class` opened holds the
        methods' decisions and is not listed, so they count toward none."""
        self._state = self._state_global
        self.context.current_function = self.context.stacked_functions.pop()


class _SwitchBody:
    """One open switch body: where it began and what its current arm has read.

    An arm is a pattern and the block it runs (about_Switch). The pattern is
    whatever sits directly in the body before that block: a value, a string,
    `default`, or a script block of its own, `{ $_ -gt 5 }`. So a `{` opening
    directly in the body starts the arm's block when a pattern came before it,
    and is itself the pattern when none did.
    """

    __slots__ = ("depth", "pattern", "in_pattern")

    def __init__(self, depth: int):
        self.depth = depth        # the brace depth the body's `{` opened at
        self.pattern = None       # the current arm's pattern: None, _ARM or _DEFAULT_ARM
        self.in_pattern = False   # a script-block pattern is open

    def read(self, token: str) -> None:
        """A token directly in the body, other than a brace."""
        if token == ";":
            self.pattern = None
        elif self.pattern is None:
            self.pattern = _DEFAULT_ARM if token == _DEFAULT_ARM else _ARM

    def opens_arm(self) -> bool:
        """A `{` directly in the body: True when it runs an arm that tests
        something, the one point the arm costs."""
        pattern, self.pattern = self.pattern, None
        self.in_pattern = pattern is None
        return pattern == _ARM

    def closes(self) -> None:
        """A `}` back at the body's own depth: a closed script block is a pattern."""
        if self.in_pattern:
            self.in_pattern, self.pattern = False, _ARM


class SwitchArmStates(CodeStateMachine):
    """One condition per switch arm, the `default` arm free.

    Runs as a parallel state next to the machine that finds functions, and
    reports through the same `context.add_condition()` hook lizard's own
    `condition_counter` uses, so an arm lands on whichever function lizard has
    open — the same attribution every other condition gets.

    An arm has no keyword to match, so it is found by position: the block that
    opens directly inside a switch body after a pattern (see `_SwitchBody`).
    That needs the brace depth, which needs to know where the switch body
    began, which is why this machine carries a stack rather than a flag.
    Nested switches are why the stack is a list.

    `switch` opens a switch only where it starts a statement, so `git switch
    main` and `[switch]$Force` open nothing. The flags and subject between the
    word and its body can hold brackets, semicolons and script blocks inside
    their parentheses (`switch ($m['k'])`); only a `;` or a `}` outside them
    ends a switch that opened no body.
    """

    def __init__(self, context):
        super().__init__(context)
        self._subject = None   # parentheses open in a switch's subject; None when no switch waits
        self._bodies = []      # one _SwitchBody per open switch body, innermost last
        self._depth = 0
        self._previous = ""    # the last token read, block comments aside
        self._line = 0         # the line it ended on

    def _state_global(self, token):
        if token.startswith(_BLOCK_COMMENT):
            return
        statement = _begins_statement(self._previous, self.context.current_line != self._line)
        self._dispatch(token, statement)
        self._previous, self._line = token, self.context.current_line

    def _dispatch(self, token: str, statement: bool) -> None:
        if self._subject is not None:
            self._read_subject(token)
        elif token == "switch" and statement:
            self._subject = 0
        else:
            self._read(token)

    def _read_subject(self, token: str) -> None:
        if self._subject == 0 and token in _SUBJECT_ENDS:
            self._end_subject(token)
        else:
            self._subject += _PAREN_CHANGE.get(token, 0)
            self._depth += _BRACE_CHANGE.get(token, 0)

    def _end_subject(self, token: str) -> None:
        self._subject = None
        if token == "{":
            self._bodies.append(_SwitchBody(self._depth))
            self._depth += 1
        else:
            self._read(token)  # a `;` or a `}`: the word opened no switch

    def _read(self, token: str) -> None:
        if token == "{":
            self._open_brace()
        elif token == "}":
            self._close_brace()
        elif self._in_body():
            self._bodies[-1].read(token)

    def _open_brace(self) -> None:
        if self._in_body() and self._bodies[-1].opens_arm():
            self.context.add_condition()
        self._depth += 1

    def _close_brace(self) -> None:
        self._depth -= 1
        if self._bodies and self._bodies[-1].depth == self._depth:
            self._bodies.pop()
        elif self._in_body():
            self._bodies[-1].closes()

    def _in_body(self) -> bool:
        """Whether the brace depth sits directly inside the innermost switch body."""
        return bool(self._bodies) and self._depth == self._bodies[-1].depth + 1


class PowerShellReader(CodeReader, ScriptLanguageMixIn):
    """See the module docstring for the counting convention and its limits."""

    ext = ["ps1", "psm1"]
    language_names = ["powershell"]

    _control_flow_keywords = {"if", "elseif", "for", "foreach", "while",
                              "until", "catch", "trap"}
    # `&&` and `||` are PowerShell 7's pipeline chains and, as the tokenizer
    # spells them, `-and` and `-or`. `??` and `??=` evaluate their right side
    # only for a null left one.
    _logical_operators = {"&&", "||", "-xor", "??", "??="}
    _case_keywords = set()      # arms are counted by position, see the docstring
    _ternary_operators = {"?"}

    # analyze.py's modified column adds a point for a `switch` and takes one
    # back per `case`. PowerShell arms have no `case`, so the point would stay:
    # the opener gets none here, and a switch costs its arms in both columns.
    _modified_switch = False

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [PowerShellStates(context), SwitchArmStates(context)]

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        """lizard's shared tokenizer plus PowerShell's own rules, with every
        subexpression inside a double-quoted string read as code.

        ScriptLanguageMixIn supplies the `#` line-comment rule (PythonReader
        uses the same one), so comment handling is not written here. Nothing is
        rewritten in the source and nothing is materialized: the subexpressions
        are opened by a generator over lizard's, and `_spelled` respells keyword
        tokens one at a time as they come, so the token stage still yields as it
        reads, which is what crapkit's two-chain analyze.py depends on
        (tests/unit/test_cognitive_reader_chain.py).
        """
        return _spelled(_tokens(source_code, addition, token_class))


def _tokens(source: str, addition: str, token_class):
    """lizard's shared tokenizer with PowerShell's rules, every subexpression opened."""
    return _open_holes(ScriptLanguageMixIn.generate_common_tokens(
        source, _TOKEN_ADDITION + addition, token_class), addition, token_class)


def _open_holes(tokens, addition: str, token_class):
    for token in tokens:
        if token[:1] == '"' and "$(" in token:
            yield from _hole_tokens(token, addition, token_class)
        else:
            yield token


def _hole_tokens(token: str, addition: str, token_class):
    """TOKEN's subexpressions as code, and the text around each as a string token.

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


# Captured before register() wraps it, so a test can ask what lizard shipped.
_stock_languages = lizard_languages.languages


def register():
    """Make `lizard_languages.get_reader_for` resolve .ps1 and .psm1 here.

    Idempotent: a second call is a no-op, and wrapping composes with any other
    reader registered the same way, because each wrapper calls the one it
    replaced. Appending rather than prepending leaves every stock reader's
    claim intact.

    The guard asks the list whether it already carries this reader rather than
    stamping the function it installed, for the reason lizardshell.register()
    spells out: a stamp only answers for the outermost wrapper, so the second
    reader to register makes the first one's guard blind and a later call
    appends it twice.
    """
    if PowerShellReader in lizard_languages.languages():
        return PowerShellReader
    inner = lizard_languages.languages

    def languages():
        return inner() + [PowerShellReader]

    lizard_languages.languages = languages
    return PowerShellReader


register()
