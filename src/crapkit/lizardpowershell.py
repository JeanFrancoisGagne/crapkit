"""A PowerShell (.ps1/.psm1) reader for lizard, which ships none.

WHAT IS REPORTED
    Named function-shaped declarations, in all four spellings PowerShell has:
    `function Name { }`, `filter Name { }`, `workflow Name { }` and
    `configuration Name { }`. A parameter list in the header
    (`function Name ($a, $b) { }`) is read as parameters; the far more common
    `function Name { param(...) }` reports zero, because `param()` is a
    statement in the body and not a header.

    Top-level script code is NOT reported. Statements outside any declaration
    belong to lizard's `*global*` pseudo-function, exactly like Python module
    level and exactly like the shell reader beside this one, so a 189-line
    watchdog script whose only declaration is one nested helper reports one
    function. That is the answer, not a parse failure.

    An anonymous script block assigned to a variable (`$run = { ... }`) is not
    reported either: it has no name to key a ratchet row on.

CCN CONVENTION
    Conditions counted: `if`, `elseif`, `for`, `foreach`, `while`, `until`,
    `catch`, `trap`, `-and`, `-or`, `-xor`, `?`, and one point per `switch`
    arm. `else` costs nothing, as everywhere else in lizard.

    `switch` counts per arm, not once for the block. PowerShell writes arms as
    bare patterns with no `case` keyword:

        switch ($x) {
            1 { ... }
            'a' { ... }
            default { ... }
        }

    so the arm is the `{` that opens directly inside the switch body, and the
    `default` arm is free. That mirrors lizard's own C `switch`/`case` handling
    and both readers already in this package: the shell reader counts a `case`
    arm at its `;;` and the Rust reader counts a `match` arm at its `=>`, each
    for the same reason. Counting the keyword once instead would score a
    twelve-arm dispatcher the same 2 as a one-arm one. Measured on the
    hand-counted probe, a six-arm switch with a default scores 7, the same as
    the six-branch if/elseif chain doing the same work.

    `switch` itself is deliberately NOT a control-flow keyword, and that is
    load-bearing beyond the double count: `[switch]$Force` is how PowerShell
    declares a boolean parameter, and 138 `param()` blocks in the 106-script
    corpus this reader was built against are full of them. Left in the keyword
    set, every advanced function paid a phantom point for its own signature.

    `?` stays a ternary operator, as lizard has it everywhere. In PowerShell 7
    it is the ternary; in every version it is also the `Where-Object` alias,
    so `Get-Process | ? { $_.CPU -gt 10 }` costs a point. Both spell a branch,
    so the point is not wrong, only differently earned.

TOKENIZER
    The added alternatives are tried ahead of lizard's shared C-family rules,
    and they exist because six PowerShell constructs read as something else
    there. Each is pinned by a test.
      - `<# ... #>` block comment. Left alone, `<` and `#` tokenize apart and
        the comment's keywords and braces all count.
      - `@" ... "@` and `@' ... '@` here-strings. A here-string body is data
        that routinely holds keywords and unbalanced braces; as one token it
        contributes neither.
      - `"..."` with the backtick as the escape, not the backslash. lizard's
        rule ends a string at a backslash-escaped quote and every quote after
        it pairs off by one.
      - `'...'` with `''` as the escape, which is PowerShell's, not `\'`.
      - `$var` and `$script:var` as one token, so a scope-qualified name does
        not split on its colon.
      - `Verb-Noun` as one token, so `Get-ChildItem` is a name rather than a
        subtraction, and `-and`/`-or`/`-Path` as one token, which is what makes
        the logical operators countable at all.
    The `#` line-comment rule comes from ScriptLanguageMixIn, the same one
    PythonReader uses.

KNOWN LIMITS
    - PowerShell is case-insensitive and this reader is not: `If (` in code
      counts nothing. Measured over the 106-script corpus, all 29 capitalized
      `If`, 9 `For` and 1 `WHILE` sit inside a comment or a string, where the
      tokenizer already discards them, and not one appears in code. A repo
      that capitalizes its keywords undercounts.
    - A here-string is recognized by `@"` and `"@` alone. PowerShell also
      requires the opener to end its line and the terminator to start one;
      this reader does not check either, so `@"` inside an expression opens a
      body that runs to the next `"@`.
    - A `{` pattern arm (`switch ($x) { {$_ -gt 5} { ... } }`) counts twice:
      the pattern's own brace opens directly inside the switch body too.
    - `params` is 0 for the usual `function Name { param(...) }` shape, so the
      parameter-count column understates PowerShell.

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

import lizard_languages
from lizard_languages.code_reader import CodeReader, CodeStateMachine
from lizard_languages.golike import GoLikeStates
from lizard_languages.script_language import ScriptLanguageMixIn

# Extra alternatives for lizard's shared token pattern, tried ahead of it.
# Order matters only among alternatives that can start at the same character:
# the here-strings precede the plain strings, and both precede `$var`.
_TOKEN_ADDITION = (
    r"|<\#[\s\S]*?\#>"          # <# block comment #>
    r"|@\"[\s\S]*?\"@"          # @" here-string "@
    r"|@'[\s\S]*?'@"            # @' here-string '@
    r"|\"(?:`.|[^\"`])*\""      # "double" string, backtick is the escape
    r"|'(?:''|[^'])*'"          # 'single' string, '' is the escape
    r"|\$[\w:]+"                # $var, $script:var
    r"|[A-Za-z_]\w*(?:-\w+)+"   # Verb-Noun, one token
    r"|-\w+"                    # -and, -or, -eq, -Path
)

# Every keyword that declares something with a name and a brace body.
_FUNC_KEYWORDS = ("function", "filter", "workflow", "configuration")

# The wildcard arm of a switch, free exactly like C's `default:`.
_DEFAULT_ARM = "default"

# Tokens that prove the `switch` just seen was not a switch STATEMENT. `]`
# closes the `[switch]` type accelerator; `;` ends the statement it sat in.
_DISARMS_SWITCH = frozenset({"]", ";"})


class PowerShellStates(GoLikeStates):
    """Function detection: the Go machine, with PowerShell's two differences.

    Go declares with one keyword, `func`, and PowerShell with four, so the
    global state matches the set. Go also writes `func name(args) {` and always
    has the parameter list, while PowerShell usually writes `function Name {`
    and declares its parameters in the body, so a `{` right after the name has
    to open the body rather than end the search. Everything else — the brace
    bookkeeping, nested declarations, the name accumulation — is the inherited
    machine.
    """

    FUNC_KEYWORD = "function"

    def _state_global(self, token):
        if token in _FUNC_KEYWORDS:
            self._state = self._function_name
            self.context.push_new_function("")
        elif token == "{":
            self.sub_state(self.statemachine_clone())
        elif token == "}":
            self.statemachine_return()

    def _expect_function_dec(self, token):
        if token == "{":
            self.next(self._expect_function_impl, token)
        else:
            super()._expect_function_dec(token)


class SwitchArmStates(CodeStateMachine):
    """One condition per switch arm, the `default` arm free.

    Runs as a parallel state next to the machine that finds functions, and
    reports through the same `context.add_condition()` hook lizard's own
    `condition_counter` uses, so an arm lands on whichever function lizard has
    open — the same attribution every other condition gets.

    An arm has no keyword to match, so it is found by position: the `{` that
    opens directly inside a switch body. That needs the brace depth, which
    needs to know where the switch body began, which is why this machine
    carries a stack rather than a flag. Nested switches are why the stack is a
    list.
    """

    def __init__(self, context):
        super().__init__(context)
        self._armed = False   # a switch statement is waiting for its body brace
        self._bodies = []     # depth at which each open switch body began
        self._depth = 0
        self._previous_code_token = ""

    def _state_global(self, token):
        if token.isspace():
            return  # `default` and its `{` may sit on two lines
        self._dispatch(token)
        self._previous_code_token = token

    def _dispatch(self, token):
        if token == "{":
            self._open_brace()
        elif token == "}":
            self._close_brace()
        elif token == "switch":
            # `[switch]$Force` is a parameter declaration, not a statement.
            self._armed = self._previous_code_token != "["
        elif token in _DISARMS_SWITCH:
            self._armed = False

    def _open_brace(self) -> None:
        if self._armed:
            self._bodies.append(self._depth)
            self._armed = False
        elif self._opens_an_arm():
            self.context.add_condition()
        self._depth += 1

    def _close_brace(self) -> None:
        self._armed = False
        self._depth -= 1
        if self._bodies and self._bodies[-1] == self._depth:
            self._bodies.pop()

    def _opens_an_arm(self) -> bool:
        return (bool(self._bodies)
                and self._depth == self._bodies[-1] + 1
                and self._previous_code_token != _DEFAULT_ARM)


class PowerShellReader(CodeReader, ScriptLanguageMixIn):
    """See the module docstring for the counting convention and its limits."""

    ext = ["ps1", "psm1"]
    language_names = ["powershell"]

    _control_flow_keywords = {"if", "elseif", "for", "foreach", "while",
                              "until", "catch", "trap"}
    _logical_operators = {"-and", "-or", "-xor"}
    _case_keywords = set()      # arms are counted by position, see the docstring
    _ternary_operators = {"?"}

    # What lizard's ND extension treats as a nesting structure; its default set
    # is the C family's and mentions neither `elseif` nor `until`.
    loops = {"if", "elseif", "for", "foreach", "while", "until", "-and", "-or"}

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [PowerShellStates(context), SwitchArmStates(context)]

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        """lizard's shared tokenizer plus PowerShell's own rules.

        ScriptLanguageMixIn supplies the `#` line-comment rule (PythonReader
        uses the same one), so comment handling is not written here. Nothing is
        rewritten in the source and nothing is materialized: the token stage
        stays the single generator lizard built, which is what crapkit's
        two-chain analyze.py depends on (tests/unit/test_cognitive_reader_chain.py).
        """
        return ScriptLanguageMixIn.generate_common_tokens(
            source_code, _TOKEN_ADDITION + addition, token_class)


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
