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
for, while, &&, ||, ?) still count, with three corrections where the stock
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

Accepted, documented, not solved
--------------------------------
* `macro_rules!` bodies use `=>` for their own rules, so each rule adds a point
  to the function that declares the macro. Rare, and cheaper to accept than a
  macro-aware parser.
* `?` error propagation stays counted: upstream puts it in
  `_ternary_operators`, and this reader inherits that as measured rather than
  changing two things at once. tests/unit/test_lizardrust.py pins it at +1.
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


def _ends_operand(token: str | None) -> bool:
    """Whether `token` can end the left operand of a binary operator."""
    if not token or token in _NO_VALUE_KEYWORDS:
        return False
    return token[-1].isalnum() or token[-1] in _OPERAND_TAIL


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
    """The decisions Rust spells without a keyword lizard counts.

    One condition per match arm, wildcard arms free, and one per let-else.
    An `else` is a let-else's unless a `}` stands before it (see the module
    docstring).

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
        self.previous_code_token = token

    def _decides(self, token: str) -> bool:
        if token == _ARM:
            return self.previous_code_token != _WILDCARD
        return token == "else" and self.previous_code_token != "}"


class CorrectedRustStates(RustStates):
    """lizard's RustStates, with a signature that decides nothing.

    The conditions counted between `fn` and the body's `{` came from tokens
    that only declare, so the `{` sets the function back to its base of 1.
    """

    def _expect_function_impl(self, token):
        if token == "{":
            self.context.current_function.cyclomatic_complexity = 1
        super()._expect_function_impl(token)


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
