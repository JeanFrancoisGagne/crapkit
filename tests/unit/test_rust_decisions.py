"""Which Rust tokens are decisions, hand-counted.

McCabe's count (NIST SP 500-235 sec. 4.1) is 1 plus the binary decisions. The
Rust reader read four things that decide nothing as decisions, and missed one
that does:

* a `where` clause and a `?Sized` bound are part of a signature, and a
  signature decides nothing. A `for<'a>` binder in one is no loop either.
* a `||` with no operand before it opens a closure with no parameters, and a
  `&&` with no operand before it borrows twice. Neither is a logical operator,
  so neither counts in any column.
* a let-else runs its `else` block when the pattern does not match, which is
  a binary decision like the `if let` it replaces.

Every number below was counted by hand from the snippet above it.
"""
from crapkit.analyze import analyze_source


def rows(code: str) -> dict:
    return {r.long_name.split()[0]: r for r in analyze_source("src/lib.rs", code, note=False)}


def one(code: str):
    (record,) = analyze_source("src/lib.rs", code, note=False)
    return record


# The else runs when `Some(n)` does not match `a`: 1 + 1 = 2.
LET_ELSE = """pub fn let_else(a: Option<i32>) -> i32 {
    let Some(n) = a else {
        return 0;
    };
    n
}
"""

# An if-else expression bound by `let`: the `if` is the one decision, and its
# `else` follows the `}` of the if block. 1 + 1 = 2.
LET_IF_ELSE = """pub fn pick(a: bool) -> i32 {
    let n = if a { 1 } else { 2 };
    n
}
"""

# `move || n` builds a closure with no parameters. No decision: 1.
MOVE_CLOSURE = """pub fn empty_closure(n: i32) -> Box<dyn Fn() -> i32> {
    Box::new(move || n)
}
"""

# The same closure as a call argument, after `(`. No decision: 1.
CLOSURE_ARGUMENT = """pub fn fallback(v: Option<i32>) -> i32 {
    v.unwrap_or_else(|| 0)
}
"""

# `|&&x|` destructures a double reference. The one `>` compares; nothing
# decides: 1.
DOUBLE_BORROW = """pub fn positive(v: &[i32]) -> usize {
    v.iter().filter(|&&x| x > 0).count()
}
"""

# A parameter typed as a reference to a reference. No decision: 1.
DOUBLE_REFERENCE = """pub fn width(s: &&str) -> usize {
    s.len()
}
"""

# Four `||` after a name, a `]`, a `)` and `true`, and one `&&` after a literal:
# 1 + 4 + 1 = 6.
LOGICAL = """pub fn either(a: bool, v: &[bool], n: i32) -> bool {
    a || v[0] || f(a) || true || n > 0 && a
}
"""

# A ?Sized bound relaxes a default. No decision: 1.
MAYBE_SIZED = """pub fn maybe_sized<P: AsRef<str> + ?Sized>(p: &P) -> usize {
    p.as_ref().len()
}
"""

# A trait bound in a where clause. No decision: 1.
WHERE_CLAUSE = """pub fn with_where<E>(v: &str) -> Result<i32, E>
where
    E: Error,
{
    parse(v)
}
"""

# A higher-ranked bound `for<'a>` names a lifetime; the `if` is the one
# decision: 1 + 1 = 2.
HIGHER_RANKED = """pub fn call<F>(f: F) -> usize
where
    F: for<'a> Fn(&'a str) -> usize,
{
    if f("x") > 0 {
        return 1;
    }
    0
}
"""

# A nested fn's signature is its own. outer: the if, 1 + 1 = 2. inner: 1.
NESTED_SIGNATURE = """pub fn outer(a: bool) -> i32 {
    if a {
        return 1;
    }
    fn inner<T: ?Sized>(t: &T) -> i32 where T: Debug {
        0
    }
    2
}
"""


def test_a_let_else_is_one_decision():
    record = one(LET_ELSE)

    assert (record.ccn_std, record.ccn_mod) == (2, 2)


def test_the_else_of_an_if_expression_is_not_a_second_decision():
    assert one(LET_IF_ELSE).ccn_std == 2


def test_a_move_closure_with_no_parameters_decides_nothing():
    record = one(MOVE_CLOSURE)

    assert (record.ccn_std, record.cognitive, record.nesting) == (1, 0, 0)


def test_a_closure_with_no_parameters_as_an_argument_decides_nothing():
    record = one(CLOSURE_ARGUMENT)

    assert (record.ccn_std, record.cognitive, record.nesting) == (1, 0, 0)


def test_a_double_borrow_in_a_closure_pattern_decides_nothing():
    record = one(DOUBLE_BORROW)

    assert (record.ccn_std, record.cognitive, record.nesting) == (1, 0, 0)


def test_a_double_reference_parameter_decides_nothing():
    """The `&&` reads as the two borrows it is, so the long name spells it
    `& &`: the one place this change moves a function's key."""
    record = one(DOUBLE_REFERENCE)

    assert (record.ccn_std, record.cognitive, record.nesting) == (1, 0, 0)
    assert record.long_name == "width s : & & str"


def test_logical_operators_after_an_operand_still_count():
    """The guard on the rule above: a `||` or `&&` after a name, a closing
    bracket or a literal is the operator, whatever the operand."""
    record = one(LOGICAL)

    assert (record.ccn_std, record.cognitive) == (6, 2)


def test_a_maybe_sized_bound_decides_nothing():
    assert one(MAYBE_SIZED).ccn_std == 1


def test_a_where_clause_decides_nothing():
    assert one(WHERE_CLAUSE).ccn_std == 1


def test_a_higher_ranked_bound_is_no_loop():
    assert one(HIGHER_RANKED).ccn_std == 2


def test_a_nested_signature_resets_only_its_own_function():
    found = rows(NESTED_SIGNATURE)

    assert (found["outer"].ccn_std, found["inner"].ccn_std) == (2, 1)
