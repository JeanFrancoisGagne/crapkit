"""A Rust `match` costs cognitive complexity, the way a `switch` always has.

The construct crapkit built a whole corrected reader for was invisible in the
second column. `_COUNTING` holds `switch` and not `match`, so a five-arm
dispatch scored 0 cognitive while the if/else-if chain doing the same work
scored 6, and a Rust worklist sorted by readability read the dispatch table as
the simplest function in the file.

Sonar's rule for a switch is +1 plus the nesting it sits in, with the arms
free — one decision, however many ways it goes — and nesting rises inside it.
That is the rule applied here, so a match and a switch cost the same thing.

Rust only. `match` is a soft keyword in Python: the same spelling is an
identifier anywhere else, and a rule that read it everywhere would move Python
scores off a variable named `match`. The reader is the discriminator, exactly as
it is for the C++ rvalue-reference rule next to it.
"""
from crapkit.analyze import ANALYSIS_VERSION, analyze_source

# Five arms that match a value plus the wildcard: one decision, +1 at nesting 0.
CLASSIFY = """fn classify(n: i32) -> &'static str {
    match n {
        0 => "zero",
        1 => "one",
        2 => "two",
        3 => "three",
        4 => "four",
        _ => "many",
    }
}
"""

# The same dispatch spelled as a chain: if +1, four else-if +4, else +1 = 6.
CLASSIFY_CHAIN = """fn classify_chain(n: i32) -> &'static str {
    if n == 0 {
        "zero"
    } else if n == 1 {
        "one"
    } else if n == 2 {
        "two"
    } else if n == 3 {
        "three"
    } else if n == 4 {
        "four"
    } else {
        "many"
    }
}
"""

# outer match +1 at nesting 0, inner match +1 +1 for the nesting = 3.
NESTED = """fn route(cmd: &Cmd) -> u8 {
    match cmd {
        Cmd::Run(mode) => match mode {
            Mode::Fast => 1,
            Mode::Slow => 2,
            _ => 3,
        },
        Cmd::Stop => 4,
        _ => 0,
    }
}
"""

# for +1 at nesting 0, match +1 +1 inside it = 3.
IN_A_LOOP = """fn drain(items: &[Cmd]) -> u8 {
    let mut n = 0;
    for item in items {
        match item {
            Cmd::Stop => return n,
            _ => n += 1,
        }
    }
    n
}
"""

# The 6-branch shape tests/unit/test_cognitive_reader_chain.py measures in four
# languages, in Rust. No match in it, so the new rule must not move it.
PROBE = """fn probe(a: i32, b: i32) -> i32 {
    let mut b = b;
    if a > 0 && b > 0 {
        for i in 0..b {
            if i == a {
                return i;
            }
        }
    } else {
        while b > 0 {
            b -= 1;
        }
    }
    0
}
"""

# A Python `match` statement, the shape the Rust rule must not reach.
PY_MATCH = '''def classify(n):
    match n:
        case 0:
            return "zero"
        case 1:
            return "one"
        case _:
            return "many"
'''


def cognitive(name: str, source: str) -> int:
    (record,) = analyze_source(name, source)
    return record.cognitive


def test_a_five_arm_match_costs_one():
    """0 before the fix. One decision, whatever the arm count, exactly as Sonar
    charges a switch."""
    assert cognitive("src/lib.rs", CLASSIFY) == 1


def test_the_arms_are_free_the_way_switch_cases_are():
    """The arm count belongs to the cyclomatic column, which counts each
    non-wildcard arm. Cognitive charges the decision once, so the two columns
    say different things about the same block on purpose: 6 and 1."""
    (record,) = analyze_source("src/lib.rs", CLASSIFY)

    assert (record.ccn, record.cognitive) == (6, 1)


def test_the_if_else_twin_still_costs_six():
    """The comparison that made the gap visible. The chain was never wrong; a
    reader that scored it 6 and its match twin 0 was."""
    assert cognitive("src/lib.rs", CLASSIFY_CHAIN) == 6


def test_a_nested_match_pays_the_nesting_increment():
    """Sonar's second half: the block deepens, so a match inside a match costs 2.
    A rule that charged a flat +1 would read this as 2 instead of 3."""
    assert cognitive("src/lib.rs", NESTED) == 3


def test_a_match_inside_a_loop_is_deepened_by_the_loop():
    assert cognitive("src/lib.rs", IN_A_LOOP) == 3


def test_the_six_branch_probe_is_untouched():
    """No match in it: the pin that the new keyword changed one construct."""
    (record,) = analyze_source("src/lib.rs", PROBE)

    assert (record.cognitive, record.ccn) == (10, 6)


def test_a_python_match_statement_is_untouched():
    """`match` is a soft keyword in Python and the rule is keyed on the reader,
    so Python's number is what it was. Widening `_COUNTING` instead would have
    moved every Python file holding a variable called `match`."""
    assert cognitive("app.py", PY_MATCH) == 0


def test_analysis_version_invalidates_the_cached_rust_cognitive_column():
    """Every cached Rust record at version 6 or below carries a cognitive score
    measured without this rule, and the cache keys on content plus the analysis
    fingerprint. Rust is the only language whose stored values move."""
    assert ANALYSIS_VERSION > 6
