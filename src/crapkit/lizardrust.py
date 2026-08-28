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
scores 8, the same as 7 ifs with no else. Everything else lizard counts for Rust
(if, for, while, where, &&, ||, ?) is untouched.

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
tests/unit/test_lizardrust.py pins the stock reader's wrong answer. It fails on
the lizard release that fixes #494. Delete this module then, along with the
`register()` call, rather than repairing it.
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


class MatchArmStates(CodeStateMachine):
    """One condition per match arm, wildcard arms free.

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
        if token == _ARM and self.previous_code_token != _WILDCARD:
            self.context.add_condition()
        self.previous_code_token = token


class CorrectedRustReader(_StockRustReader):
    """lizard's RustReader with the match rule of lizard #494 replaced.

    Subtracting `match` from the inherited keyword set (rather than restating
    the set) keeps every other keyword upstream counts, including ones a later
    lizard adds.
    """

    # pylint: disable=too-few-public-methods
    _control_flow_keywords = _StockRustReader._control_flow_keywords - {"match"}

    def __init__(self, context):
        super().__init__(context)
        self.parallel_states = [RustStates(context), MatchArmStates(context)]


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
