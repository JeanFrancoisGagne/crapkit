"""Which Rust functions exist and how many parameters each declares, by hand.

The Rust Reference (Functions, Traits, External blocks) is the source. Two
readings were wrong:

* a signature that ends in `;` declares a function with no body: a trait's
  required method or an `extern` block's foreign function. A `fn` with its `(`
  right after it is a pointer type, since Rust has no anonymous function item.
  lizard waited for a `{` through the `;`, so the next function's body became
  the signature's, and the next function got no row.
* a comma inside a parameter's type or pattern, `(char, char)` or
  `HashMap<K, V>`, parted one parameter into two.
"""
import pytest

from crapkit.analyze import analyze_source


def rows(code: str) -> dict:
    return {r.long_name.split()[0]: r for r in analyze_source("src/lib.rs", code, note=False)}


# A required trait method, then a free function: one function, after_trait.
TRAIT_SIGNATURE = """pub trait Builder {
    fn build(&mut self) -> i32;
}

pub fn after_trait(n: i32) -> i32 {
    n + 1
}
"""

# A required method, a provided one with an if (ccn 2), a required one whose
# return type holds a `;`, then a free function: two functions.
TRAIT_MIXED = """pub trait Shape {
    fn area(&self) -> f64;
    fn scaled(&self, k: f64) -> f64 {
        if k > 1.0 {
            return self.area() * k;
        }
        self.area()
    }
    fn bytes(&self) -> [u8; 4];
}

pub fn last(n: i32) -> i32 {
    n
}
"""

# A foreign function, then a free function: one function, after_extern.
EXTERN_BLOCK = """extern "C" {
    fn abs(x: i32) -> i32;
}

pub fn after_extern(n: i32) -> i32 {
    n
}
"""

# A `fn` pointer type in a let: fn_pointer owns its if, 1 + 1 = 2.
FN_POINTER = """pub fn fn_pointer(n: i32) -> i32 {
    let f: fn(i32) -> i32 = add_one;
    if n > 0 {
        return f(n);
    }
    0
}
"""

# A `fn` pointer type in a struct literal, where the `}` comes before any `;`:
# table owns its if, 1 + 1 = 2, and after_table follows it.
FN_POINTER_IN_A_LITERAL = """pub fn table(n: i32) -> Ops {
    let ops = Ops { run: add_one as fn(i32) -> i32 };
    if n > 0 {
        return ops;
    }
    ops
}

pub fn after_table(n: i32) -> i32 {
    n
}
"""

# A macro's input holds a signature with no `;`; the macro's `}` ends it, and
# after_macro keeps its row and its if: 1 + 1 = 2.
SIGNATURE_IN_A_MACRO = """define! { fn hook(x: i32) -> i32 }

pub fn after_macro(n: i32) -> i32 {
    if n > 0 { 1 } else { 0 }
}
"""

# A body whose return type holds a `;`: listed with its lines 1-3.
ARRAY_RETURN = """pub fn array_return(n: u8) -> [u8; 4] {
    [n; 4]
}
"""

# glob, r and c: three parameters, one of them a tuple.
TUPLE_PARAM = """pub fn tuple_param(glob: &str, r: &mut (char, char), c: char) -> bool {
    glob.is_empty()
}
"""

# A tuple pattern, a map of tuples and a closure type: three parameters.
NESTED_COMMAS = """pub fn nested(
    (a, b): (i32, i32),
    m: HashMap<K, (A, B)>,
    f: impl Fn(A, B) -> C,
) -> i32 {
    a + b
}
"""


def test_a_required_trait_method_hides_no_function():
    found = rows(TRAIT_SIGNATURE)

    assert sorted(found) == ["after_trait"]
    assert (found["after_trait"].start, found["after_trait"].end) == (5, 7)


def test_a_trait_lists_its_provided_methods_and_the_function_after_it():
    found = rows(TRAIT_MIXED)

    assert sorted(found) == ["last", "scaled"]
    assert (found["scaled"].start, found["scaled"].end, found["scaled"].ccn_std) == (3, 8, 2)
    assert (found["last"].start, found["last"].end) == (12, 14)


def test_a_foreign_function_hides_no_function():
    found = rows(EXTERN_BLOCK)

    assert sorted(found) == ["after_extern"]


def test_a_fn_pointer_type_keeps_the_block_after_it():
    found = rows(FN_POINTER)

    assert sorted(found) == ["fn_pointer"]
    assert (found["fn_pointer"].ccn_std, found["fn_pointer"].cognitive) == (2, 1)


def test_a_fn_pointer_type_in_a_literal_ends_at_the_literal_brace():
    """lizard listed no `table` at all: an anonymous function took its if."""
    found = rows(FN_POINTER_IN_A_LITERAL)

    assert sorted(found) == ["after_table", "table"]
    assert (found["table"].start, found["table"].end, found["table"].ccn_std) == (1, 7, 2)


@pytest.mark.parametrize("pointer_type", [
    "let hs: Vec<fn(i32) -> bool> = vec![];",
    "let hs = Vec::<fn()>::new();",
    "let hs: HashMap<&str, fn(i32) -> i32> = HashMap::new();",
    "let hs: Option<fn()> = None;",
])
def test_a_fn_pointer_type_inside_a_generic_keeps_the_block_after_it(pointer_type):
    """The `>` after the pointer type closes a bracket opened before its `fn`,
    so no `;` ever read as the pointer's end. An anonymous row took the while
    block and its two decisions: run is 1 + while + && = 3, cognitive 2."""
    code = ("pub fn run(n: i32) -> i32 {\n"
            f"    {pointer_type}\n"
            "    let mut i = 0;\n"
            "    while i < n && hs.is_empty() {\n"
            "        i += 1;\n"
            "    }\n"
            "    i\n"
            "}\n")
    found = analyze_source("src/lib.rs", code, note=False)

    assert [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive) for r in found] == [
        ("run n : i32", 1, 8, 3, 2)]


def test_a_brace_closing_the_block_around_a_signature_ends_it():
    found = rows(SIGNATURE_IN_A_MACRO)

    assert sorted(found) == ["after_macro"]
    assert (found["after_macro"].start, found["after_macro"].ccn_std) == (3, 2)


def test_a_semicolon_inside_a_return_type_ends_nothing():
    found = rows(ARRAY_RETURN)

    assert (found["array_return"].start, found["array_return"].end) == (1, 3)


def test_a_tuple_typed_parameter_is_one_parameter():
    """The long name is the ratchet key and keeps its spelling."""
    (record,) = analyze_source("src/lib.rs", TUPLE_PARAM, note=False)

    assert record.params == 3
    assert record.long_name == "tuple_param glob : & str , r : & mut char , char , c : char"


def test_commas_inside_a_pattern_or_a_type_part_no_parameters():
    (record,) = analyze_source("src/lib.rs", NESTED_COMMAS, note=False)

    assert record.params == 3


def test_commas_inside_a_struct_pattern_part_no_parameters():
    """The Rust Reference (Functions): a parameter is a pattern, and a struct
    pattern's fields sit in braces. Two parameters, and the name as before."""
    code = "pub fn struct_pattern(Point { x, y }: Point, _: i32) -> i32 {\n    x + y\n}\n"
    (record,) = analyze_source("src/lib.rs", code, note=False)

    assert record.params == 2
    assert record.long_name == "struct_pattern Point { x , y } : Point , _ : i32"


def test_a_comma_before_any_parameter_token_parts_nothing():
    """`((), n)` reaches its comma with every token so far a parenthesis,
    which the long name never spells. One parameter, and the name as before."""
    code = "pub fn unit_pair(((), n): ((), i32)) -> i32 {\n    n\n}\n"
    (record,) = analyze_source("src/lib.rs", code, note=False)

    assert record.params == 1
    assert record.long_name == "unit_pair , n : , i32"
