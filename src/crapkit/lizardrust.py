"""A Rust reader that counts match arms. Upstream defect: lizard #494.

lizard 1.24.0 lists `match` in `RustReader._control_flow_keywords` and counts
arms zero times, so the whole block costs 1 no matter how many ways it branches.
A 7-arm match scores ccn 2 and the if/else-if chain that does the same work
scores 4. Every Rust worklist built on that reads a dispatch table as trivial.

The convention here mirrors lizard's own C `switch` handling, where each `case`
counts and `default` does not:

  * each arm's `=>` counts one condition, except the wildcard arm (a bare `_`
    immediately before the `=>`, newlines between them allowed)
  * `match` itself counts nothing

A match with 6 conditional arms and a `_` arm scores 7, the same as the
equivalent if/else-if/else chain. An exhaustive match of 7 non-wildcard arms
scores 8, the same as 7 ifs with no else. lizard's other Rust conditions (if,
for, while, &&, ||, ?) still count, with four corrections where the stock
reader read a Rust token as the C token of the same spelling:

  * a let-else's `else` counts one. It runs when the pattern does not match,
    the decision an `if let` makes. An if-else's `else` always follows the `}`
    of its if block, and a let-else's never can (the Rust Reference refuses a
    let-else whose expression ends in `}`), so the token before `else` tells
    the two apart.
  * a signature decides nothing. Everything from `fn` to the body's `{` only
    declares: a `where` clause, a `?Sized` bound, a `for<'a>` binder. The
    body's `{` sets the function back to its base of 1, the way lizard's C
    reader confirms a function at its body, and `where` leaves the keyword set
    for the items it bounds outside a signature.
  * a `||` or `&&` with no operand before it is no operator. `move || n` opens
    a closure with no parameters and `|&&x|` takes a double reference. The
    reader splits such a pair into its two characters before any column reads
    it, so ccn, cognitive and nesting all see the `|x|` or `&x` they already
    read as nothing. A parameter typed `&&T` spells `& &` in its long name.
  * a `for` that is no loop decides nothing, in a body too. Rust spells three
    things `for`: a loop, a `for<'a>` binder, which has a `<` right after it,
    and the `for` of `impl Trait for Type`, which has the trait's name or the
    `>` of its arguments right before it. A loop starts a statement or an
    expression, so no name stands before one. The `?` of a `?Sized` bound is
    no decision either, wherever it stands. `RustDecisionStates` takes back
    the point lizard counted for each.

Three more corrections decide which functions exist and what they declare, in
`CorrectedRustStates`: a signature that reaches a `;` or a `}` before any `{`
has no body and is listed as no function, a `fn` with its `(` right after it
is a pointer type and opens no function wherever it stands, and a comma inside
a parameter's type or pattern parts no parameters. `loops` hands lizard's
nesting column Rust's structures in place of C's.

Accepted, documented, not solved
--------------------------------
* `macro_rules!` bodies use `=>` for their own rules, so each rule adds a point
  to the function that declares the macro. Rare, and cheaper to accept than a
  macro-aware parser.
* `?` error propagation stays counted: upstream puts it in
  `_ternary_operators`, and this reader inherits that as measured rather than
  changing two things at once. tests/unit/test_lizardrust.py pins it at +1.
* lizard's nesting column reads the `for` of a `for<'a>` binder, and of an
  `impl Trait for Type` inside a function, as a loop, one level where there is
  none. It reads keywords with no context around them, and gluing the binder
  into one token would break the `<`/`>` count lizard uses to skip a generic
  parameter list. test_rust_cognitive_nesting.py and test_rust_for.py pin it.
* `ccn_mod` (analyze.py's modified column) is unchanged, so a Rust match now
  costs the same in both columns. lizard's modified pass keys off
  `reader._keyword_match`, which upstream never sets for Rust; setting it here
  would add a point for the block and subtract nothing for the arms.

Registration
------------
lizard resolves a reader by extension in `lizard_languages.get_reader_for`,
which walks the hardcoded list `lizard_languages.languages()` and returns the
first class whose `ext` matches. Nothing scans `CodeReader.__subclasses__` in
1.24.0 (`extra_subclasses` exists and is read by no one), so defining a subclass
registers nothing and import order decides nothing either.

`languages()` reads each reader class out of the `lizard_languages` namespace on
every call, so `register()` rebinds one name there, keeping the list order and
lizard's own function. `lizard.py` did `from lizard_languages import
get_reader_for` at import, and that function object still resolves classes
through the `lizard_languages` globals, so the rebind reaches
`FileAnalyzer.analyze_source_code` too. `register()` verifies that through
`lizard.get_reader_for` and raises if it did not take.

The contract for the caller (analyze.py owns the wiring):

  1. call `register()` at module scope, before any `FileAnalyzer` runs
  2. call it from the module the process pool imports, so spawned workers
     register in their own interpreter
  3. bump `ANALYSIS_VERSION`, because cached Rust records predate the fix

Retirement
----------
tests/unit/test_lizardrust.py pins the stock reader's wrong answers, one per
correction, and each pin fails on the lizard release that fixes its defect. Drop
a correction when its pin fails. Once every pin fails, delete this module along
with the `register()` call rather than repairing it.
"""
from __future__ import annotations

from ._pygdefer import deferred_pygments

with deferred_pygments():  # lizard's Erlang reader would load pygments here
    import lizard
    import lizard_languages
    from lizard_languages.code_reader import CodeStateMachine
    from lizard_languages.rust import RustReader as _StockRustReader
    from lizard_languages.rust import RustStates

_ARM = "=>"
_WILDCARD = "_"

# Any filename picks the reader; the file is never opened.
_PROBE = "crapkit_registration_probe.rs"

# The Rust Reference's keywords, strict and reserved, less the five that can end
# an operand: self, Self, true, false and the `await` of `.await`. A `||` or
# `&&` right after one of these has no left operand.
_NO_VALUE_KEYWORDS = frozenset("""
    abstract as async become box break const continue crate do dyn else enum
    extern final fn for gen if impl in let loop macro match mod move mut override
    priv pub ref return static struct super trait try type typeof unsafe unsized
    use virtual where while yield
""".split())

# The last character of a token that can end an operand, beside the word
# characters of a name or a number: a closing bracket, the `?` of error
# propagation, a name's trailing `_`, a string or char literal's quote.
_OPERAND_TAIL = frozenset(")]}?_\"'")

# The two operators Rust also writes as a pair of one-character tokens.
_PAIRS = frozenset({"||", "&&"})

# Two code tokens that declare, where lizard counted the first as a decision:
# a `for<'a>` binder and a `?Sized` bound.
_DECLARING_PAIRS = frozenset({("for", "<"), ("?", "Sized")})

# What a bracket inside a signature does to its type depth. Parentheses are
# counted apart, by the state machine that reads the parameter list. In that
# list a `{` also opens a struct pattern's fields, `Point { x, y }: Point`;
# after it, a `{` opens the body.
_TYPE_DEPTH = {"<": 1, "[": 1, ">": -1, "]": -1}
_PARAMETER_DEPTH = {**_TYPE_DEPTH, "{": 1, "}": -1}


def _ends_operand(token: str | None) -> bool:
    """Whether `token` can end the left operand of a binary operator."""
    if not token or token in _NO_VALUE_KEYWORDS:
        return False
    return token[-1].isalnum() or token[-1] in _OPERAND_TAIL


def implements_for(previous: str | None) -> bool:
    """Whether a `for` right after `previous` is the `for` of `impl Trait for
    Type`, which is no loop.

    The trait's name or the `>` closing its arguments stands before that
    `for`. A loop starts a statement or an expression, so what stands before a
    loop's is a `;`, a brace, an attribute's `]`, a `=`, a label's `:` or an
    arm's `=>`, never a name.
    """
    if previous == ">":
        return True
    return _ends_operand(previous) and (previous[0].isalpha() or previous[0] == "_")


def _code_token(token: str, previous: str | None) -> str | None:
    """The last code token once `token` is read: whitespace and comments leave it."""
    if token.isspace() or token.startswith(("//", "/*")):
        return previous
    return token


def split_operator_pairs(tokens):
    """Each `||` and `&&` with no operand before it, as its two characters.

    lizard's tokenizer reads `||` and `&&` greedily, so `move || n` and
    `|&&x|` carried a logical operator that ccn, cognitive and nesting all
    counted. A binary operator needs a left operand; without one the `||` is
    a closure's empty parameter list and the `&&` two borrows.
    """
    previous = None
    for token in tokens:
        if token in _PAIRS and not _ends_operand(previous):
            yield from token
        else:
            yield token
        previous = _code_token(token, previous)


class RustDecisionStates(CodeStateMachine):
    """The decisions Rust spells without a keyword lizard counts, and the
    tokens lizard counts that decide nothing in Rust.

    One condition per match arm, wildcard arms free, and one per let-else.
    An `else` is a let-else's unless a `}` stands before it (see the module
    docstring). lizard's count loses one for each `for` that is no loop and
    for the `?` of each `?Sized`, wherever they stand.

    Runs as a parallel state of the reader, next to the RustStates machine that
    finds functions, and reports through the same `context.add_condition()` hook
    lizard's `condition_counter` uses. Needs its own lookbehind because
    `CodeStateMachine.last_token` records the newline tokens that survive
    preprocessing, and a wildcard arm may wrap between the `_` and the `=>`.
    """

    def __init__(self, context):
        super().__init__(context)
        self.previous_code_token = None

    def _state_global(self, token):
        if token == "\n":
            return
        if self._decides(token):
            self.context.add_condition()
        elif self._declares(token):
            self.context.add_condition(-1)
        self.previous_code_token = token

    def _decides(self, token: str) -> bool:
        if token == _ARM:
            return self.previous_code_token != _WILDCARD
        return token == "else" and self.previous_code_token != "}"

    def _declares(self, token: str) -> bool:
        """A token that takes back a condition lizard counted: the `for` of an
        implementation, the `<` that makes the `for` before it a binder, and
        the `Sized` that makes the `?` before it a relaxed bound."""
        if token == "for":
            return implements_for(self.previous_code_token)
        return (self.previous_code_token, token) in _DECLARING_PAIRS


def _join_comma(fn) -> None:
    """A comma inside one parameter's type or pattern, kept in that parameter.

    The long name gets the ` ,` lizard has always written there, because the
    long name is the ratchet key.
    """
    fn.add_to_long_name(" ,")
    if fn.full_parameters:
        fn.full_parameters[-1] += " ,"


class CorrectedRustStates(RustStates):
    """lizard's RustStates, with a signature read the way Rust declares it.

    * The conditions counted between `fn` and the body's `{` came from tokens
      that only declare, so the `{` sets the function back to its base of 1.
    * A signature that reaches a `;` or a `}` before any `{` has no body: a
      trait's required method, an `extern` block's foreign function, a
      signature in a macro's input. It is listed as no function. lizard waited
      for a `{` through both, so the next function's body became the
      signature's and the next function got no row.
    * A `fn` pointer type, `fn(i32) -> bool`, is no function either. It is
      told apart at its `(`, because inside `Vec<fn()>` the `;` after it is
      not at the signature's depth.
    * A comma inside a parameter's type or pattern, `(char, char)`,
      `HashMap<K, V>` or `Point { x, y }`, parts no parameters.

    `type_depth` counts the `<` and `[` open in the signature, and the `{` of a
    struct pattern in its parameter list, so the comma in `HashMap<K, V>` and
    the `;` in `-> [u8; 4]` read as the type's own.
    """

    def __init__(self, context):
        super().__init__(context)
        self.type_depth = 0

    def _function_name(self, token):
        """A `fn` right before a `(` spells a pointer type and opens no
        function. Rust has no anonymous function item, and a pointer type
        can stand inside brackets opened before it, `Vec<fn()>`, where no `;`
        after it is at the signature's depth."""
        if token == "(":
            self._no_body(token)
        else:
            super()._function_name(token)

    @CodeStateMachine.read_inside_brackets_then("()", "_expect_function_impl")
    def _function_dec(self, token):
        if token in "()":
            return
        self.type_depth += _PARAMETER_DEPTH.get(token, 0)
        if token == "," and self._nested():
            _join_comma(self.context.current_function)
        else:
            self.context.parameter(token)

    def _nested(self) -> bool:
        """Inside a bracket of the parameter's own: a parenthesis, an angle
        bracket, a square bracket or a struct pattern's brace."""
        return self.br_count > 1 or self.type_depth > 0

    def _expect_function_impl(self, token):
        if token == "{":
            self._body(token)
        elif token in (";", "}") and self.type_depth == 0:
            self._no_body(token)
        else:
            self.type_depth += _TYPE_DEPTH.get(token, 0)

    def _body(self, token):
        self.type_depth = 0
        self.context.current_function.cyclomatic_complexity = 1
        super()._expect_function_impl(token)

    def _no_body(self, token):
        """Drop the function the signature opened and hand `token` to the block
        around it, where a `}` closes that block."""
        self.type_depth = 0
        self.context.current_function = self.context.stacked_functions.pop()
        self.next(self._state_global, token)


class CorrectedRustReader(_StockRustReader):
    """lizard's RustReader with the match rule of lizard #494 replaced.

    Subtracting from the inherited keyword set (rather than restating the set)
    keeps every other keyword upstream counts, including ones a later lizard
    adds.
    """

    # pylint: disable=too-few-public-methods
    _control_flow_keywords = _StockRustReader._control_flow_keywords - {"match", "where", "catch"}

    # The tokens lizard's nesting column (lizard_ext/lizardnd.py) opens a level
    # on, which it takes from a reader's `loops` before its own default. That
    # default is C's: it holds `?`, which Rust spells for error propagation and
    # `?Sized`, and `case`, `def`, `catch`, `foreach` and `try`, which Rust code
    # uses as names (`for case in cases`), and it lacks `loop`. `match` stays
    # out as upstream has it: lizard opens a level per `case`, and a Rust match
    # has none.
    loops = frozenset({"if", "for", "while", "loop", "&&", "||"})

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [CorrectedRustStates(context), RustDecisionStates(context)]

    @staticmethod
    def generate_tokens(source_code, addition="", token_class=None):
        return split_operator_pairs(
            _StockRustReader.generate_tokens(source_code, addition, token_class))


def register() -> None:
    """Make lizard resolve `.rs` to CorrectedRustReader. Idempotent.

    Raises RuntimeError when the rebind does not reach lizard's own resolution,
    which is what a lizard release that stops reading `languages()` out of
    module globals would do. A Rust file measured with the defective reader is
    worse than a crash: the score is wrong and looks fine.
    """
    lizard_languages.RustReader = CorrectedRustReader
    resolved = lizard.get_reader_for(_PROBE)
    if resolved is not CorrectedRustReader:
        raise RuntimeError(_unregistered(resolved))


def _unregistered(resolved: type | None) -> str:
    name = getattr(resolved, "__name__", "no reader")
    return (f"crapkit.lizardrust.register() did not take: lizard resolves '.rs' to "
            f"{name}, not CorrectedRustReader. lizard {lizard.version} picks readers "
            f"some other way than lizard_languages.languages(); rewrite register() "
            f"against the new mechanism, or drop this module if #494 is fixed.")
