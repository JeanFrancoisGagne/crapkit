"""Rust's cognitive and nesting columns read Rust's own structures, hand-counted.

The Sonar paper (Cognitive Complexity v1.7, App. B1-B3) charges each loop and
condition +1 plus the nesting it sits in, and each of those opens a nesting
level. An early return is no increment. Three Rust spellings missed that:

* `?` propagates an error, an early return, and in `?Sized` relaxes a bound.
  Both read as a C ternary: +1 and a nesting level.
* `loop` is a loop and read as nothing, so the `if` inside it cost 1, not 2.
* `catch`, `switch`, `foreach`, `case` and `def` are no Rust keywords, and a
  name spelled that way read as a structure.

Every number below was counted by hand from the snippet above it.
"""
from crapkit.analyze import analyze_source


def one(code: str, path: str = "src/lib.rs"):
    (record,) = analyze_source(path, code, note=False)
    return record


# `?` returns early on an error: no increment, no level. Its ccn point stays.
TRY_OP = """pub fn try_op(a: &str) -> Result<i32, E> {
    let n = parse(a)?;
    Ok(n)
}
"""

# The if +1 at nesting 0; the `?` inside it adds nothing: cognitive 1, nesting 1.
TRY_IN_AN_IF = """pub fn load(p: &str, strict: bool) -> Result<u8, E> {
    if strict {
        let v = read(p)?;
        return Ok(v);
    }
    Ok(0)
}
"""

# A ?Sized bound: no structure at all.
MAYBE_SIZED = """pub fn maybe_sized<P: AsRef<str> + ?Sized>(p: &P) -> usize {
    p.as_ref().len()
}
"""

# loop +1, the if inside it +1 +1 for the nesting = 3; the if body is two deep.
SPIN = """pub fn spin(n: i32) -> i32 {
    let mut i = 0;
    loop {
        if i > n {
            break;
        }
        i += 1;
    }
    i
}
"""

# loop +1, for +2, if +3, the break to a label +1 = 7; three levels deep.
LABELED = """pub fn search(grid: &[Vec<i32>]) -> bool {
    'rows: loop {
        for row in grid {
            if row.is_empty() {
                break 'rows;
            }
        }
        return true;
    }
    false
}
"""

# `case` names a loop variable: the for is the one structure, one level deep.
CASE_VARIABLE = """pub fn case_names(cases: Vec<Case>) -> i32 {
    for case in cases {
        let a = case.x;
        let b = case.y;
    }
    0
}
"""

# Three method calls whose names other languages keep as keywords: nothing.
FOREIGN_KEYWORDS = """pub fn calls(p: Promise) -> i32 {
    p.catch(1);
    p.switch(2);
    p.foreach(3);
    0
}
"""

# A `for<'a>` binder in a where clause names a lifetime; the if is the one
# structure, +1 at nesting 0.
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


def test_error_propagation_is_no_increment_and_no_level():
    record = one(TRY_OP)

    assert (record.cognitive, record.nesting, record.ccn_std) == (0, 0, 2)


def test_error_propagation_inside_an_if_costs_only_the_if():
    record = one(TRY_IN_AN_IF)

    assert (record.cognitive, record.nesting) == (1, 1)


def test_a_maybe_sized_bound_is_no_structure():
    record = one(MAYBE_SIZED)

    assert (record.ccn_std, record.cognitive, record.nesting) == (1, 0, 0)


def test_loop_is_a_loop():
    record = one(SPIN)

    assert (record.cognitive, record.nesting, record.ccn_std) == (3, 2, 2)


def test_a_labeled_loop_nests_what_it_holds():
    record = one(LABELED)

    assert (record.cognitive, record.nesting) == (7, 3)


def test_a_variable_named_case_opens_no_level():
    record = one(CASE_VARIABLE)

    assert (record.cognitive, record.nesting, record.ccn_std) == (1, 1, 2)


def test_names_other_languages_keep_as_keywords_decide_nothing():
    record = one(FOREIGN_KEYWORDS)

    assert (record.ccn_std, record.ccn_mod, record.cognitive, record.nesting) == (1, 1, 0, 0)


def test_a_higher_ranked_binder_is_no_loop_in_cognitive():
    """Accepted: lizard's nesting column reads the binder's `for` as a loop and
    counts a level for it, 2 where the hand count is 1. The binder sits in the
    signature, and that column reads no context around a keyword."""
    record = one(HIGHER_RANKED)

    assert (record.ccn_std, record.cognitive, record.nesting) == (2, 1, 2)


def test_a_name_spelled_switch_is_no_switch_in_python_or_shell():
    """ccn_mod adds 1 for a `switch` block, and neither language has one: a
    Python parameter named `switch` read ccn_mod 3 for a function with no
    decision, and a shell variable 2. The gated ccn takes the lower column and
    never moved."""
    python = one("def pick(switch):\n    return switch\n", path="pick.py")
    shell = one('pick() {\n  switch=1\n  echo "$switch"\n}\n', path="pick.sh")

    assert (python.ccn_std, python.ccn_mod) == (1, 1)
    assert (shell.ccn_std, shell.ccn_mod) == (1, 1)


def test_a_typescript_switch_still_counts_once():
    """The guard on the rule above: a real switch is +1 in ccn_mod and each of
    its cases -1, so a switch of two cases reads ccn_std 3 and ccn_mod 2."""
    code = ("export function pick(x: number): number {\n"
            "  switch (x) {\n    case 1: return 1;\n    case 2: return 2;\n"
            "    default: return 0;\n  }\n}\n")
    record = one(code, path="src/pick.ts")

    assert (record.ccn_std, record.ccn_mod) == (3, 2)
