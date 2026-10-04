"""Istanbul parser seam: coverage-final.json content in, per-file function coverage out. Pure."""
import copy
import json

import pytest

from crapkit.score import FnCoverage
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
# refuse the artifact naming the file and the entry. A branch with no loc
# attached to no function; it now sits on the `line` producers write beside
# loc. A branch with neither was left out, and the function it sat in lost its
# arms with nothing said; it now refuses the artifact naming the branch id.
# Each field's refusal, dropped or retyped, is a row of coverage_fields.FIELDS
# that tests/unit/test_coverage_reader_contract.py runs through read().

def _with(mutate) -> str:
    art = copy.deepcopy(ARTIFACT)
    mutate(art["C:\\repo\\src\\app.ts"])
    return json.dumps(art)


def _set(entry: dict, key: str, value) -> None:
    if value == "absent":
        entry.pop(key, None)
    else:
        entry[key] = value


@pytest.mark.parametrize("loc", ["absent", None, {"end": {"line": 2}}],
                         ids=["loc-absent", "loc-null", "start-absent"])
def test_a_branch_with_no_loc_attaches_by_the_line_beside_it(loc):
    def mutate(cov):
        cov["branchMap"]["0"]["line"] = 2
        _set(cov["branchMap"]["0"], "loc", loc)

    per_file = parse_istanbul(_with(mutate), repo_root="C:\\repo")

    assert per_file == parse_istanbul(json.dumps(ARTIFACT), repo_root="C:\\repo")


def test_the_lanes_page_quotes_the_branch_refusal():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    with pytest.raises(ToolError) as raised:
        parse_istanbul(_with(lambda cov: cov["branchMap"]["1"].pop("loc")), repo_root="C:\\repo")
    reason = str(raised.value).split(".json: ", 1)[1]

    page = (root / "docs" / "lanes.md").read_text(encoding="utf-8")
    assert f"/repo/.crapkit/cov/js/coverage-final.json: {reason}" in page


# Every other shape a file's fields arrive in, and the bytes the artifact
# arrives in, is a row of the conformance suite every coverage adapter runs
# through read(): tests/unit/test_coverage_reader_contract.py.


# --- a mapped id with no hit record ---------------------------------------------
#
# istanbul pairs every fnMap, statementMap and branchMap entry with a counter in
# f, s and b. A salvage merged by hand, or a converter, can drop one, and each
# read the absent counter as a zero: a statement never ran, a branch pair did not
# exist, a function was never called. The score moved with nothing on stderr,
# and a dropped branch record flipped add-tests to ok.

STATEMENTS = {
    "C:\\repo\\src\\hot.ts": {
        "path": "C:\\repo\\src\\hot.ts",
        "fnMap": {"0": {"name": "hot", "decl": {"start": {"line": 1}},
                        "loc": {"start": {"line": 1}, "end": {"line": 6}}}},
        "f": {"0": 1},
        "branchMap": {"0": {"loc": {"start": {"line": 2}},
                            "locations": [{"start": {"line": 2}}, {"start": {"line": 4}}]}},
        "b": {"0": [1, 0]},
        "statementMap": {str(i): {"start": {"line": i}, "end": {"line": i}} for i in (2, 3, 5)},
        "s": {"2": 1, "3": 1, "5": 0},
    }
}
KEY = "C:\\repo\\src\\hot.ts"


def _without(group: str, key: str | None) -> dict:
    artifact = copy.deepcopy(STATEMENTS)
    if key is None:
        del artifact[KEY][group]
    else:
        del artifact[KEY][group][key]
    return artifact


DROPPED = {
    "statement-hit-record-missing": ("s", "3", "statement '3' has no hit count in `s`"),
    "every-statement-hit-missing": ("s", None, "statement '2' has no hit count in `s`"),
    "branch-hit-record-missing": ("b", "0", "branch '0' has no hit count in `b`"),
    "function-hit-record-missing": ("f", None, "function '0' has no hit count in `f`"),
}


@pytest.mark.parametrize("form", sorted(DROPPED))
def test_a_mapped_id_with_no_hit_record_refuses_the_artifact_and_names_it(form):
    group, key, named = DROPPED[form]

    with pytest.raises(ToolError) as raised:
        parse_istanbul(json.dumps(_without(group, key)), repo_root="C:\\repo")

    message = str(raised.value)
    assert f"src/hot.ts: {named}, so crapkit cannot tell whether it ran" in message, message
    assert "regenerate the artifact with the coverage tool" in message, message


def test_the_dead_line_reader_refuses_the_same_artifact():
    """The missing-lines read (explain, brief, diff coverage) holds the same rule,
    or a dropped counter reads there as a line that never ran."""
    from coverage_readers import parse_istanbul_missing

    with pytest.raises(ToolError, match="statement '3' has no hit count"):
        parse_istanbul_missing(json.dumps(_without("s", "3")), repo_root="C:\\repo")


def test_every_counter_present_reads_as_before():
    per_file = parse_istanbul(json.dumps(STATEMENTS), repo_root="C:\\repo")

    (hot,) = per_file["src/hot.ts"]
    assert (hot.invoked, hot.branches_total, hot.branches_covered) == (True, 2, 1)
    assert (hot.statements_total, hot.statements_covered) == (3, 2)


def test_a_file_that_maps_nothing_needs_no_counters():
    """An empty map pairs with an absent counter group: nothing is missing."""
    bare = {KEY: {"path": KEY, "fnMap": {}, "statementMap": {}, "branchMap": {}}}

    assert parse_istanbul(json.dumps(bare), repo_root="C:\\repo") == {"src/hot.ts": []}


def test_the_lanes_page_quotes_the_refusal_a_dropped_counter_draws():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    artifact = {"C:\\repo\\src\\hot.ts": _without("s", "3")[KEY]}

    with pytest.raises(ToolError) as raised:
        parse_istanbul(json.dumps(artifact), repo_root="C:\\repo")

    reason = str(raised.value).split(": ", 1)[1]
    assert f"coverage/ui.json: {reason}" in (root / "docs" / "lanes.md").read_text(encoding="utf-8")


# --- a branch whose hit counts do not match its locations ---------------------
#
# istanbul writes one hit count in `b` per location of a branchMap entry. The
# attribution counted the hit counts, so an if/else whose array was cut to [1]
# scored 1 of 1 branches, where [1, 0] scored 1 of 2, and an empty array read as
# a function with no branch and fell back to its statements.

def _branch_hits(hits: list) -> dict:
    artifact = copy.deepcopy(STATEMENTS)
    artifact[KEY]["b"]["0"] = hits
    return artifact


MISCOUNTED = {"one-of-two": [1], "none-of-two": [], "three-of-two": [1, 0, 1]}


@pytest.mark.parametrize("form", sorted(MISCOUNTED))
def test_a_branch_whose_hit_counts_do_not_match_its_locations_refuses_the_artifact(form):
    hits = MISCOUNTED[form]

    with pytest.raises(ToolError) as raised:
        parse_istanbul(json.dumps(_branch_hits(hits)), repo_root="C:\\repo")

    message = str(raised.value)
    assert (f"src/hot.ts: branch '0' has {len(hits)} hit count(s) in `b` for its 2 location(s), "
            "so crapkit cannot tell which of its paths ran (1 such in this file)") in message, message
    assert message.endswith("regenerate the artifact with the coverage tool, or merge shards "
                            "with one that keeps every counter"), message


def test_the_dead_line_reader_refuses_a_branch_cut_short_too():
    from coverage_readers import parse_istanbul_missing

    with pytest.raises(ToolError, match="branch '0' has 1 hit count"):
        parse_istanbul_missing(json.dumps(_branch_hits([1])), repo_root="C:\\repo")


def test_the_lanes_page_quotes_the_refusal_a_branch_cut_short_draws():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]

    with pytest.raises(ToolError) as raised:
        parse_istanbul(json.dumps(_branch_hits([1])), repo_root="C:\\repo")

    reason = str(raised.value).split(": ", 1)[1]
    assert f"coverage/ui.json: {reason}" in (root / "docs" / "lanes.md").read_text(encoding="utf-8")


def test_a_branch_record_with_no_locations_counts_its_hit_counts():
    """A hand-built branchMap entry may carry only `loc`. Its hit counts are then
    the only count of its paths there is, and they are read as before."""
    artifact = copy.deepcopy(STATEMENTS)
    del artifact[KEY]["branchMap"]["0"]["locations"]

    (hot,) = parse_istanbul(json.dumps(artifact), repo_root="C:\\repo")["src/hot.ts"]
    assert (hot.branches_total, hot.branches_covered) == (2, 1)


# --- every key through the reported entry -------------------------------------
#
# The reader stripped the root off each key as literal text. A report made from
# a shell standing in `c:\...`, reached through a junction or symlink, or keyed
# with the `\\?\` prefix named this checkout in a spelling the text strip missed,
# so every key stayed absolute and the lane FAILED, telling the user to point
# the reporter at the checkout it already measured. Each key now goes through
# repopath.Reported, whose spelling rows live in test_repopath.py.

from crapkit import coverage_istanbul as _adapter
from crapkit.config import Lane as _Lane
from crapkit.repopath import Reported as _Reported


def test_the_reader_keys_every_file_through_the_reported_entry(tmp_path, monkeypatch):
    """A counting stand-in for Reported sees each key of the report once, and
    the reader keys each file by what it answered."""
    seen: list[str] = []

    class Counted(_Reported):
        def __call__(self, raw: str) -> str:
            seen.append(raw)
            return super().__call__(raw)

    body = ARTIFACT["C:\\repo\\src\\app.ts"]
    keys = (str(tmp_path / "src" / "a.ts"), "src/b.ts", "C:\\elsewhere\\c.ts")
    artifact = tmp_path / "coverage-final.json"
    artifact.write_text(json.dumps({key: dict(body, path=key) for key in keys}), encoding="utf-8")
    lane = _Lane(name="unit", command="x", artifact="coverage-final.json", parser="istanbul",
                 scopes=("src",))
    monkeypatch.setattr(_adapter, "Reported", Counted)

    per_file, dead, _ = _adapter.read(lane, tmp_path, artifact)
    expected = [_Reported(tmp_path)(key) for key in keys]

    assert sorted(seen) == sorted(keys)
    assert sorted(per_file) == sorted(dead) == sorted(expected)
    assert "src/a.ts" in expected and "src/b.ts" in expected


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


def test_only_an_instrumenters_own_entry_lists_every_function():
    """A file entry with statements is istanbul's own output, so a function
    its fnMap leaves out was skipped by an ignore hint. An entry built by hand
    with fnMap alone cannot say what it left out."""
    instrumented = parse_istanbul(json.dumps(DECLARED_ARROWS), repo_root="C:/repo")["src/arrows.js"]
    hand_built = parse_istanbul(json.dumps(ARTIFACT), repo_root="C:\\repo")["src/app.ts"]

    assert ({fn.full_listing for fn in instrumented}, {fn.full_listing for fn in hand_built}) == (
        {True}, {False})
