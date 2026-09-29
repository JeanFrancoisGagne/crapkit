"""Rust spells three things `for`, and only one of them is a loop, hand-counted.

The Rust Reference names them: a loop (`for x in v`, Loop expressions), a
higher-ranked binder (`for<'a>`, Trait and lifetime bounds) and the `for` of
an implementation (`impl Trait for Type`, Implementations). lizard read each
`for` as a loop: +1 in ccn, +1 and a nesting level in cognitive. A signature
already reads as nothing, so the two that are no loop cost a point only in a
body: a binder in a `let`'s type or a turbofish, and a trait implemented
inside a function, the way a test defines a stub it needs.

The token next to the `for` tells them apart. A binder's `for` has a `<`
right after it. An implementation's `for` has the trait's name, or the `>`
of its arguments, right before it; a loop's never does, because a loop
starts a statement or an expression.

Every number below was counted by hand from the snippet above it.
"""
from crapkit.analyze import analyze_source


def rows(code: str) -> dict:
    return {r.long_name.split()[0]: r for r in analyze_source("src/lib.rs", code, note=False)}


# A binder in a let's type names a lifetime. The for loop and the if in it are
# the two decisions: ccn 1 + 2 = 3, cognitive for +1, if +2 = 3.
BINDER_IN_A_LET = """pub fn binder_let(n: i32) -> i32 {
    let f: Box<dyn for<'a> Fn(&'a u8) -> &'a u8> = Box::new(pick);
    for _ in 0..n {
        if n > 1 {
            return 1;
        }
    }
    0
}
"""

# A binder in a turbofish between an if and its block. The if still opens the
# level the inner if sits in: ccn 1 + 2 = 3, cognitive if +1, inner if +2 = 3.
BINDER_IN_A_CONDITION = """pub fn binder_condition(x: &dyn Any, n: i32) -> i32 {
    if let Some(f) = x.downcast_ref::<Box<dyn for<'a> Fn(&'a u8)>>() {
        if n > 1 {
            return 1;
        }
    }
    0
}
"""

# Two traits implemented inside a function, one generic with a ?Sized bound and
# one whose trait takes arguments. The if-else is the one decision: stubs ccn
# 1 + 1 = 2, cognitive if +1, else +1 = 2. Each method is a row of its own.
IMPLEMENTATIONS = """pub fn stubs(n: i32) -> i32 {
    impl<T: ?Sized> Show for T {
        fn show(&self) -> i32 {
            1
        }
    }
    impl From<u8> for Wrapper {
        fn from(b: u8) -> Wrapper {
            Wrapper(b)
        }
    }
    if n > 0 { 1 } else { 0 }
}
"""

# Loops after a `}`, an attribute's `]`, a label's `:`, a match arm's `=>` and
# a `=`. ccn 1 + if + 5 fors + 1 non-wildcard arm = 8. Cognitive if +1, four
# fors at nesting 0 +4, match +1, the arm's for at nesting 1 +2 = 8.
LOOPS = """pub fn loops(v: &[i32], k: Kind) -> i32 {
    if v.is_empty() {
        return 0;
    }
    for x in v {}
    #[allow(unused_variables)]
    for y in v {}
    'outer: for z in v {}
    let _ = for u in v {};
    match k {
        Kind::A => for w in v {},
        _ => {}
    }
    0
}
"""


def test_a_binder_in_a_let_is_no_loop():
    """The nesting column reads the cognitive pass, which reads the binder's
    `for` as no loop: the hand count, 2. lizard's nesting column, which the row
    used to read, counted a level for it, 3."""
    found = rows(BINDER_IN_A_LET)["binder_let"]

    assert (found.ccn_std, found.cognitive, found.nesting) == (3, 3, 2)


def test_a_binder_between_an_if_and_its_block_leaves_the_if_its_level():
    found = rows(BINDER_IN_A_CONDITION)["binder_condition"]

    assert (found.ccn_std, found.cognitive) == (3, 3)


def test_a_trait_implemented_inside_a_function_is_no_loop():
    found = rows(IMPLEMENTATIONS)

    assert sorted(found) == ["from", "show", "stubs"]
    assert (found["stubs"].ccn_std, found["stubs"].cognitive) == (2, 2)
    assert (found["show"].ccn_std, found["from"].ccn_std) == (1, 1)


def test_the_nesting_column_reads_no_loop_in_an_implementation():
    """A trait implemented inside a function opens no level: the hand count, 0.
    The nesting column reads the cognitive pass, which reads the `for` of `impl
    Trait for Type` as no loop; lizard's nesting column read it as one, 1."""
    code = ("pub fn guard() -> Guard {\n"
            "    impl Drop for Guard {\n"
            "        fn drop(&mut self) {}\n"
            "    }\n"
            "    Guard\n"
            "}\n")
    found = rows(code)["guard"]

    assert (found.ccn_std, found.cognitive, found.nesting) == (1, 0, 0)


def test_a_loop_counts_whatever_stands_before_it():
    """The guard on the rule above: a loop's `for` starts a statement or an
    expression, after a brace, a bracket, a label, an arm or a `=`."""
    found = rows(LOOPS)["loops"]

    assert (found.ccn_std, found.cognitive) == (8, 8)
