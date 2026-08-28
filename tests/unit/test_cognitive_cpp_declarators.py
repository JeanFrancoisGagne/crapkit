"""In C++ and Objective-C++, `&&` before the body declares, it does not decide.

`void take(std::string &&s) {}` has no branches and no decisions, and the
cognitive pass scored it 1 — one per rvalue reference, and 2 for a function
taking two of them. Measured on the probe battery's move-semantics file: 8
points of cognitive complexity on functions whose hand count is 0.

The tokens are identical to a logical and, so no lexical rule separates them:
`T &&t` and `a && b` are both `IDENT`, `&&`, `IDENT`. Position separates them.
A C++ function's declarator — parameter list, ref-qualifier, `operator&&` name,
member-initializer list — is everything before the body's opening brace, and a
statement can only live after it. So the rule is: under lizard's two C-family
readers only (`CLikeReader` for C/C++, `ObjCReader` for `.m`/`.mm`), a `&&` seen
while the function's brace depth is still 0 declares something.

Three things this deliberately does not do, each pinned below as residue:
`auto &&x = ...` inside a body still costs 1, because that binding is past the
opening brace; the name of an `operator||` overload still costs 1, because only
`&&` doubles as a declarator; and a logical `&&` in a default argument now costs
nothing, which is what lizard's cyclomatic column already did with the whole
parameter list. Both directions are pinned, because the failure mode of an
over-broad fix is a logical `&&` that silently stops counting.
"""
from crapkit.analyze import analyze_source

RVALUE_PARAMS = """void take_one(std::string &&s) {
}
void take_two(std::string &&a, std::vector<int> &&b) {
}
template <typename T>
void forward_one(T &&t) {
    sink(std::forward<T>(t));
}
"""

MEMBERS = """struct Holder {
    Holder(Holder &&o) noexcept {}
    Holder &operator=(Holder &&o) noexcept { return *this; }
    int get() && { return 1; }
    bool operator&&(const Holder &o) const { return true; }
};
"""

# One rvalue parameter each side of the comma, one real logical and in the body.
MIXED = """bool both_nonempty(std::string &&a, std::string &&b) {
    return !a.empty() && !b.empty();
}
"""

BODY_AND = """int guarded(int a, int b) {
    if (a > 0 && b > 0) {
        return 1;
    }
    return 0;
}
"""

# `auto &&` binds a forwarding reference inside the body. The token sits past the
# opening brace, so the position rule cannot reach it.
AUTO_RVALUE = """void first_of(const std::vector<int> &v) {
    auto &&first = v.front();
    (void)first;
}
"""

# A logical `and` in a default argument: the one real operator the rule loses.
DEFAULT_ARG = """bool decide(int a, bool flag = (kAlpha && kBeta)) {
    if (a > 0) return flag;
    return false;
}
"""

OPERATOR_OR = """struct Logical {
    bool operator||(const Logical &o) const { return true; }
};
"""

TS_AND = """export function guarded(a: number, b: number): number {
  if (a > 0 && b > 0) {
    return 1;
  }
  return 0;
}
"""

PY_AND = """def guarded(a, b):
    if a > 0 and b > 0:
        return 1
    return 0
"""

# Objective-C++ is C++ with Objective-C bolted on, and `.m` and `.mm` share one
# reader. `take` is C++ move semantics; the method below is Objective-C.
OBJCPP = """#import <Foundation/Foundation.h>
#include <string>

void take(std::string &&s) {
}

@implementation Probe
- (BOOL)bothWithA:(BOOL)a b:(BOOL)b {
    if (a && b) {
        return YES;
    }
    return NO;
}
@end
"""


def _cognitive(rel_path: str, code: str) -> dict[str, int]:
    return {r.long_name.split("(")[0].strip(): r.cognitive
            for r in analyze_source(rel_path, code)}


# --- the declarator is free ---------------------------------------------------

def test_rvalue_reference_parameters_cost_nothing():
    """Three empty bodies. Every point here was an rvalue reference read as an
    `and`, and the second function was charged twice for taking two."""
    assert _cognitive("src/move.cpp", RVALUE_PARAMS) == {
        "take_one": 0, "take_two": 0, "forward_one": 0}


def test_a_move_constructor_a_ref_qualifier_and_an_operator_name_cost_nothing():
    """The other three spellings of a `&&` that is not an operator: a move
    constructor's parameter, the `&&` ref-qualifier after an empty parameter
    list, and the name of an `operator&&` overload."""
    assert _cognitive("src/holder.cpp", MEMBERS) == {
        "Holder::Holder": 0, "Holder::operator =": 0,
        "Holder::get": 0, "Holder::operator &&": 0}


def test_the_cyclomatic_column_never_counted_them_and_still_does_not():
    """lizard's own ccn reads these functions as straight-line code. The fix is
    to the cognitive extension alone, so ccn must not move either way."""
    records = analyze_source("src/move.cpp", RVALUE_PARAMS)

    assert [r.ccn for r in records] == [1, 1, 1]


# --- the operator still counts ------------------------------------------------

def test_a_logical_and_in_the_body_still_counts_beside_rvalue_parameters():
    """The test that would catch an over-broad fix: two declarator `&&` in the
    parameter list and one operator `&&` in the body, on one function."""
    assert _cognitive("src/both.cpp", MIXED) == {"both_nonempty": 1}


def test_a_guard_clause_costs_its_if_and_its_and():
    assert _cognitive("src/guard.cpp", BODY_AND) == {"guarded": 2}


def test_a_c_file_pays_the_same_rules_as_a_cpp_one():
    """One reader serves both suffixes, so the rule cannot be keyed on the
    suffix; this pins that it is not."""
    assert _cognitive("src/guard.c", BODY_AND) == {"guarded": 2}


def test_an_auto_rvalue_binding_in_the_body_is_the_documented_residue():
    """`auto &&first` is a declarator too, but it is past the opening brace and
    the position rule cannot see that. It scores 1. Pinned so the gap reads as a
    decision rather than as an oversight — widening the rule to reach it means
    reading types, which is a parser, not an extension."""
    assert _cognitive("src/first.cpp", AUTO_RVALUE) == {"first_of": 1}


def test_the_name_of_an_operator_or_overload_is_the_second_residue():
    """Only `&&` doubles as a type declarator, so the rule is keyed on `&&`
    alone and `operator||` keeps its point. Widening it to `||` would buy this
    one case and lose every `||` a default argument writes."""
    assert _cognitive("src/logical.cpp", OPERATOR_OR) == {"Logical::operator ||": 1}


def test_a_default_argument_condition_is_what_the_rule_costs():
    """The price of a position rule, paid where it is cheapest. `decide` scores
    1 for its `if` and nothing for the `&&` in its default argument.

    lizard's cyclomatic column drops the whole parameter list already — this
    same function reads ccn 2, the base plus the `if` — so the two columns now
    agree about that region instead of disagreeing about it."""
    (record,) = analyze_source("src/decide.cpp", DEFAULT_ARG)

    assert (record.cognitive, record.ccn) == (1, 2)


# --- every other language is untouched ----------------------------------------

def test_typescript_logical_ands_are_untouched():
    """`&&` is only ever an operator outside the C family, and the rule is gated
    on the reader. A TypeScript guard keeps if +1 and && +1."""
    assert _cognitive("web/guard.ts", TS_AND) == {"guarded": 2}


def test_python_logical_ands_are_untouched():
    assert _cognitive("src/guard.py", PY_AND) == {"guarded": 2}


def test_objective_cpp_takes_the_rule_and_its_methods_keep_their_ands():
    """`.mm` is C++ with Objective-C bolted on and carries move semantics
    wholesale, so `ObjCReader` is the second reader the rule covers. Both halves
    of one file, in one assertion: the C++ move sink costs nothing, the
    Objective-C method still pays for its `if` and its `&&`."""
    assert _cognitive("ios/Probe.mm", OBJCPP) == {"take": 0, "bothWithA:": 2}


def test_a_java_logical_and_is_untouched():
    """`JavaReader` subclasses `CLikeReader`. Java has no rvalue references, so
    a rule that reached it would only ever lose a real operator."""
    java = ("class Guard {\n"
            "    int guarded(int a, int b) {\n"
            "        if (a > 0 && b > 0) {\n"
            "            return 1;\n"
            "        }\n"
            "        return 0;\n"
            "    }\n"
            "}\n")

    assert _cognitive("src/Guard.java", java) == {"Guard::guarded": 2}
