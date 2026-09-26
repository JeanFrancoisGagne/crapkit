"""Istanbul parser seam: coverage-final.json content in, per-file function coverage out. Pure."""
import json

from crapkit.coverage_istanbul import FnCoverage
from coverage_readers import parse_istanbul

ARTIFACT = {
    "C:\\repo\\src\\app.ts": {
        "path": "C:\\repo\\src\\app.ts",
        "fnMap": {
            "0": {"name": "dispatch", "decl": {"start": {"line": 1}}, "loc": {"start": {"line": 1}, "end": {"line": 13}}},
            "1": {"name": "plain", "decl": {"start": {"line": 14}}, "loc": {"start": {"line": 14}, "end": {"line": 20}}},
            "2": {"name": "untouched", "decl": {"start": {"line": 22}}, "loc": {"start": {"line": 22}, "end": {"line": 25}}},
        },
        "f": {"0": 3, "1": 0, "2": 0},
        "branchMap": {
            "0": {"loc": {"start": {"line": 2}}, "locations": [{"start": {"line": 2}}, {"start": {"line": 3}}]},
            "1": {"loc": {"start": {"line": 15}}, "locations": [{"start": {"line": 15}}, {"start": {"line": 16}}]},
            "2": {"loc": {"start": {"line": 23}}, "locations": [{"start": {"line": 23}}]},
        },
        "b": {"0": [2, 1], "1": [0, 0], "2": [0]},
    }
}


def test_branch_coverage_maps_into_function_spans():
    per_file = parse_istanbul(json.dumps(ARTIFACT), repo_root="C:\\repo")
    fns = {fn.name: fn for fn in per_file["src/app.ts"]}
    assert fns["dispatch"] == FnCoverage(name="dispatch", start=1, end=13, invoked=True, branches_total=2, branches_covered=2)
    assert fns["plain"].invoked is False
    assert fns["plain"].branches_total == 2
    assert fns["plain"].branches_covered == 0


def test_zero_branch_function_coverage_comes_from_invocation():
    per_file = parse_istanbul(json.dumps(ARTIFACT), repo_root="C:\\repo")
    fns = {fn.name: fn for fn in per_file["src/app.ts"]}
    assert fns["untouched"].branches_total == 1
    assert fns["untouched"].coverage == 0.0
    assert fns["dispatch"].coverage == 1.0


def test_paths_are_repo_relative_forward_slash():
    per_file = parse_istanbul(json.dumps(ARTIFACT), repo_root="C:\\repo")
    assert list(per_file) == ["src/app.ts"]


def test_malformed_artifact_is_a_loud_error():
    import pytest

    from crapkit.errors import ToolError
    with pytest.raises(ToolError, match="istanbul"):
        parse_istanbul("{ not json", repo_root="C:\\repo")


def test_empty_artifact_is_a_loud_error_not_a_silent_all_untested():
    import pytest

    from crapkit.errors import ToolError
    with pytest.raises(ToolError, match="empty"):
        parse_istanbul("{}", repo_root="C:\repo")


def test_branches_attach_to_the_innermost_containing_function():
    # A callback nested inside a handler owns the branches in ITS span; the
    # invocation fallback must never report a nested function as fully
    # covered while its own branch arms sit untaken.
    import json
    art = {"C:/repo/src/a.ts": {
        "fnMap": {
            "0": {"name": "handler", "decl": {"start": {"line": 10}}, "loc": {"end": {"line": 40}}},
            "1": {"name": "cb", "decl": {"start": {"line": 20}}, "loc": {"end": {"line": 24}}},
        },
        "f": {"0": 1, "1": 1},
        "branchMap": {
            "b0": {"loc": {"start": {"line": 12}}},
            "b1": {"loc": {"start": {"line": 22}}},
        },
        "b": {"b0": [1, 0], "b1": [0, 0]},
    }}
    per_file = parse_istanbul(json.dumps(art), repo_root="C:/repo")
    by_name = {f.name: f for f in per_file["src/a.ts"]}
    assert by_name["cb"].branches_total == 2 and by_name["cb"].coverage == 0.0, \
        "nested cb owns line-22 branches; invoked-fallback 1.0 hides its untaken arms"
    assert by_name["handler"].branches_total == 2 and by_name["handler"].coverage == 0.5, \
        "the handler keeps only its own branches, not the callback's"


def _at(line, column):
    return {"line": line, "column": column}


def _range(start, end):
    return {"start": _at(*start), "end": _at(*end)}


# function outer(values) {                        1
#   const double = (value) => value * 2;         2
#   return values.map(double);                   3
# }                                              4
# const arrow = (value) => value * 2;            5
DECLARED_ARROWS = {"C:/repo/src/arrows.js": {
    "fnMap": {
        "0": {"name": "outer", "decl": _range((1, 9), (1, 14)), "loc": _range((1, 23), (4, None))},
        "1": {"name": "(anonymous_1)", "decl": _range((2, 17), (2, 18)), "loc": _range((2, 28), (2, 37))},
        "2": {"name": "(anonymous_2)", "decl": _range((5, 14), (5, 15)), "loc": _range((5, 25), (5, 34))},
    },
    "f": {"0": 1, "1": 0, "2": 0},
    "statementMap": {
        "0": _range((2, 8), (2, 37)), "1": _range((2, 28), (2, 37)), "2": _range((3, 2), (3, 28)),
        "3": _range((5, 14), (5, 34)), "4": _range((5, 25), (5, 34)),
    },
    "s": {"0": 1, "1": 0, "2": 1, "3": 1, "4": 0},
    "branchMap": {}, "b": {},
}}


def test_an_arrows_declaration_counter_belongs_to_the_code_around_the_arrow():
    """istanbul counts `const f = (x) => ...` with a statement that starts
    before the arrow's body and runs when the declaration does, at import for
    a module-level arrow. It is the enclosing code's statement: an arrow no
    test calls reads 0, never 0.5."""
    by_start = {fn.start: fn for fn in parse_istanbul(json.dumps(DECLARED_ARROWS),
                                                     repo_root="C:/repo")["src/arrows.js"]}

    assert [(fn.name, fn.statements_total, fn.statements_covered, fn.coverage)
            for _, fn in sorted(by_start.items())] == [
        ("outer", 2, 2, 1.0), ("(anonymous_1)", 1, 0, 0.0), ("(anonymous_2)", 1, 0, 0.0)]


# function choose(flag = false) {                 1
#   return flag ? (x) => x : null;               2
# }                                              3
CHOSEN_ARROW = {"C:/repo/src/choose.js": {
    "fnMap": {
        "0": {"name": "choose", "decl": _range((1, 9), (1, 15)), "loc": _range((1, 30), (3, None))},
        "1": {"name": "(anonymous_1)", "decl": _range((2, 16), (2, 17)), "loc": _range((2, 23), (2, 24))},
    },
    "f": {"0": 1, "1": 0},
    "statementMap": {"0": _range((2, 2), (2, 32)), "1": _range((2, 23), (2, 24))},
    "s": {"0": 1, "1": 0},
    "branchMap": {
        "0": {"type": "default-arg", "loc": _range((1, 16), (1, 28)),
              "locations": [_range((1, 23), (1, 28))]},
        "1": {"type": "cond-expr", "loc": _range((2, 9), (2, 31)),
              "locations": [_range((2, 16), (2, 24)), _range((2, 27), (2, 31))]},
    },
    "b": {"0": [1], "1": [1, 0]},
}}


def test_a_branch_that_opens_before_a_function_on_its_line_is_the_enclosers():
    """The ternary starts before the arrow it can return, and it is taken or
    not whether the arrow is ever called. A default parameter's arm sits
    between a function's name and its body, and it stays the function's."""
    by_name = {fn.name: fn for fn in parse_istanbul(json.dumps(CHOSEN_ARROW),
                                                    repo_root="C:/repo")["src/choose.js"]}

    assert (by_name["choose"].branches_total, by_name["choose"].branches_covered) == (3, 2)
    assert (by_name["(anonymous_1)"].branches_total, by_name["(anonymous_1)"].coverage) == (0, 0.0)
