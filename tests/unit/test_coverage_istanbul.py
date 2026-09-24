"""Istanbul parser seam: coverage-final.json content in, per-file function coverage out. Pure."""
import copy
import json

import pytest

from crapkit.coverage_istanbul import FnCoverage
from crapkit.errors import ToolError
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


# --- fnMap and branchMap entries with no loc ----------------------------------
#
# `fn.get("loc", {})` read a function with no loc as a span of one line, its
# declaration's, so the branches in its body attached to nothing and an invoked
# function scored as covered. A loc that was null refused the artifact with an
# AttributeError. Every istanbul producer writes loc.end.line, so both now
# refuse the artifact naming the entry. A branch with no loc attached to no
# function; it now sits on the `line` producers write beside loc. A branch with
# neither is still left out:
# tests/e2e/test_explain_gaps.py::test_istanbul_branches_without_a_loc_line_and_outside_every_span_are_ignored

def _with(mutate) -> str:
    art = copy.deepcopy(ARTIFACT)
    mutate(art["C:\\repo\\src\\app.ts"])
    return json.dumps(art)


def _set(entry: dict, key: str, value) -> None:
    if value == "absent":
        entry.pop(key, None)
    else:
        entry[key] = value


@pytest.mark.parametrize("loc", ["absent", None, {}, {"start": {"line": 1}},
                                 {"end": {"line": None}}, {"end": {"line": "13"}}],
                         ids=["loc-absent", "loc-null", "loc-empty", "end-absent",
                              "end-line-null", "end-line-a-string"])
def test_a_function_with_no_end_line_refuses_the_artifact_naming_the_entry(loc):
    text = _with(lambda cov: _set(cov["fnMap"]["0"], "loc", loc))

    with pytest.raises(ToolError, match=r"istanbul artifact .*fnMap\['0'\] has no loc\.end\.line"):
        parse_istanbul(text, repo_root="C:\\repo")


@pytest.mark.parametrize("loc", ["absent", None, {"end": {"line": 2}}],
                         ids=["loc-absent", "loc-null", "start-absent"])
def test_a_branch_with_no_loc_attaches_by_the_line_beside_it(loc):
    def mutate(cov):
        cov["branchMap"]["0"]["line"] = 2
        _set(cov["branchMap"]["0"], "loc", loc)

    per_file = parse_istanbul(_with(mutate), repo_root="C:\\repo")

    assert per_file == parse_istanbul(json.dumps(ARTIFACT), repo_root="C:\\repo")
