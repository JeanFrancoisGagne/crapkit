"""Hand-counted Rust complexity, and the reader-resolution mechanics behind it.

Every number below was counted by hand from the snippet above it, never read off
a run. The convention under test: base 1, plus one per match arm whose pattern is
not the bare wildcard, plus lizard's existing conditions (if, for, while, where,
&&, ||, ?). `match` itself costs nothing.

test_stock_reader_scores_the_seven_arm_match_two is the retirement signal. It
pins the upstream defect, so it fails the day lizard fixes #494; that is when
src/crapkit/lizardrust.py gets deleted rather than repaired.
"""
import lizard
import lizard_languages
import pytest
from lizard_languages.rust import RustReader as StockRustReader

from crapkit.lizardrust import CorrectedRustReader, register

# 6 arms that match a value, plus the wildcard. Hand count: 1 + 6 = 7, the same
# as the if/else-if chain in IF_CHAIN extended to six branches.
MATCH_WITH_WILDCARD = """
fn classify(n: i32) -> &'static str {
    match n {
        0 => "zero",
        1 => "one",
        2 => "two",
        3 => "three",
        4 => "four",
        5 => "five",
        _ => "many",
    }
}
"""

# No wildcard: the compiler proved the 7 arms exhaustive. Hand count: 1 + 7 = 8,
# the same as 7 ifs with no else.
EXHAUSTIVE_MATCH = """
fn classify(n: u8) -> u8 {
    match n {
        0 => 10,
        1 => 11,
        2 => 12,
        3 => 13,
        4 => 14,
        5 => 15,
        6 => 16,
    }
}
"""

# Outer: 2 counted arms and a wildcard. Inner: 2 counted arms and a wildcard.
# Hand count: 1 + 2 + 2 = 5.
NESTED_MATCH = """
fn nested(a: i32, b: i32) -> i32 {
    match a {
        0 => match b {
            0 => 1,
            1 => 2,
            _ => 3,
        },
        1 => 4,
        _ => 5,
    }
}
"""

# 2 counted arms, and each carries a guard the `if` rule already counts.
# Hand count: 1 + 2 arms + 2 guards = 5.
GUARDED_MATCH = """
fn guarded(n: i32) -> i32 {
    match n {
        x if x > 10 => 1,
        x if x < 0 => 2,
        _ => 3,
    }
}
"""

# A closure's pipes are not conditions and hold no arm. Hand count: 1.
CLOSURE = """
fn adder(v: Vec<i32>) -> Vec<i32> {
    v.into_iter().map(|x| x + 1).collect()
}
"""

# 3 ifs and a final else. Hand count: 1 + 3 = 4, under both readers.
IF_CHAIN = """
fn classify(n: i32) -> &'static str {
    if n == 0 {
        "zero"
    } else if n == 1 {
        "one"
    } else if n == 2 {
        "two"
    } else {
        "many"
    }
}
"""

# Fields, no fn: lizard reports no functions at all.
STRUCT_ONLY = """
pub struct Point {
    pub x: f64,
    pub y: f64,
}
"""

# `?` is in RustReader._ternary_operators upstream. Hand count under that rule:
# 1 + 1 = 2. Left exactly as measured, in both readers.
QUESTION_MARK = """
fn read_it(p: &str) -> Result<String, Error> {
    let s = std::fs::read_to_string(p)?;
    Ok(s)
}
"""

# The wildcard and its arrow sit on different lines. Hand count: 1 + 1 = 2. A
# lookbehind that stopped at the newline token would read the wildcard arm as a
# real one and score 3.
WILDCARD_ACROSS_LINES = """
fn spread(n: i32) -> i32 {
    match n {
        0 => 1,
        _
            => 2,
    }
}
"""

# `where` stays a condition, as upstream has it. Hand count: 1 + 1 = 2.
WHERE_BOUND = """
fn generic<T>(v: T) -> T where T: Clone {
    v
}
"""

# macro_rules! arms use the same `=>`. Accepted overcount, pinned here so it
# shows up as a change rather than a surprise. Hand count of the code: 1. The
# single macro rule adds the extra point.
MACRO_RULES = """
fn uses_macro() -> i32 {
    macro_rules! twice {
        ($e:expr) => { $e * 2 };
    }
    twice!(21)
}
"""


@pytest.fixture(autouse=True)
def _restore_rs_reader():
    """Each test rebinds the reader lizard resolves for '.rs'. Put it back."""
    original = lizard_languages.RustReader
    yield
    lizard_languages.RustReader = original


def ccn(code: str, reader_cls: type) -> dict[str, int]:
    """Cyclomatic complexity per function, measured through the named reader.

    Goes through lizard.FileAnalyzer rather than the reader object, because the
    reader lizard picks for a filename is the thing under test.
    """
    lizard_languages.RustReader = reader_cls
    analyzer = lizard.FileAnalyzer(lizard.get_extensions([]))
    analysis = analyzer.analyze_source_code("sample.rs", code)
    return {fn.name: fn.cyclomatic_complexity for fn in analysis.function_list}


def test_six_arms_and_a_wildcard_score_seven():
    assert ccn(MATCH_WITH_WILDCARD, CorrectedRustReader) == {"classify": 7}


def test_exhaustive_seven_arm_match_scores_eight():
    assert ccn(EXHAUSTIVE_MATCH, CorrectedRustReader) == {"classify": 8}


def test_nested_match_counts_the_arms_of_both_levels():
    assert ccn(NESTED_MATCH, CorrectedRustReader) == {"nested": 5}


def test_a_guard_costs_its_own_point_on_top_of_its_arm():
    assert ccn(GUARDED_MATCH, CorrectedRustReader) == {"guarded": 5}


def test_closure_pipes_are_not_conditions():
    assert ccn(CLOSURE, CorrectedRustReader) == {"adder": 1}


def test_if_else_chain_is_untouched():
    assert ccn(IF_CHAIN, CorrectedRustReader) == {"classify": 4}
    assert ccn(IF_CHAIN, StockRustReader) == {"classify": 4}


def test_a_struct_alone_has_no_functions():
    assert ccn(STRUCT_ONLY, CorrectedRustReader) == {}


def test_error_propagation_stays_a_condition():
    assert ccn(QUESTION_MARK, CorrectedRustReader) == {"read_it": 2}
    assert ccn(QUESTION_MARK, StockRustReader) == {"read_it": 2}


def test_wildcard_split_across_lines_is_still_free():
    assert ccn(WILDCARD_ACROSS_LINES, CorrectedRustReader) == {"spread": 2}


def test_dropping_match_leaves_the_other_keywords_counting():
    assert ccn(WHERE_BOUND, CorrectedRustReader) == {"generic": 2}
    assert "match" not in CorrectedRustReader._build_conditions()
    assert {"if", "for", "while", "where"} <= CorrectedRustReader._build_conditions()


def test_macro_rules_arms_count_too():
    """The documented overcount. Stock reads 1, this reader reads 2."""
    assert ccn(MACRO_RULES, StockRustReader) == {"uses_macro": 1}
    assert ccn(MACRO_RULES, CorrectedRustReader) == {"uses_macro": 2}


def test_stock_reader_scores_the_seven_arm_match_two():
    """lizard #494 itself: the whole match counts once and no arm counts.

    Retire src/crapkit/lizardrust.py when this test fails.
    """
    assert ccn(MATCH_WITH_WILDCARD, StockRustReader) == {"classify": 2}
    assert ccn(EXHAUSTIVE_MATCH, StockRustReader) == {"classify": 2}


def test_register_makes_lizard_resolve_rs_to_the_corrected_reader():
    """Both names are one function object, and neither is import-order sensitive:
    lizard.py binds lizard_languages.get_reader_for at import, and that function
    reads the reader classes out of the lizard_languages namespace per call."""
    lizard_languages.RustReader = StockRustReader
    register()
    assert lizard.get_reader_for("src/lib.rs") is CorrectedRustReader
    assert lizard_languages.get_reader_for("src/lib.rs") is CorrectedRustReader


def test_register_runs_twice_with_the_same_result():
    lizard_languages.RustReader = StockRustReader
    register()
    register()
    assert lizard.get_reader_for("src/lib.rs") is CorrectedRustReader


def test_register_raises_when_lizard_resolves_something_else(monkeypatch):
    """A lizard release that stops reading `languages()` out of module globals
    must break loudly here, not measure Rust with the defective reader."""
    monkeypatch.setattr(lizard, "get_reader_for", lambda _: StockRustReader)
    with pytest.raises(RuntimeError, match="RustReader"):
        register()


def test_other_languages_keep_their_readers():
    register()
    assert lizard.get_reader_for("a.py").__name__ == "PythonReader"
    assert lizard.get_reader_for("a.go").__name__ == "GoReader"
