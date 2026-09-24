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
# refuse the artifact naming the file and the entry. A branch with no loc
# attached to no function; it now sits on the `line` producers write beside
# loc. A branch with neither was left out, and the function it sat in lost its
# arms with nothing said; it now refuses the artifact naming the branch id.

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


@pytest.mark.parametrize("loc", ["absent", None, {}, {"start": None}, {"start": {"line": None}},
                                 {"start": {"line": "2"}}],
                         ids=["loc-absent", "loc-null", "loc-empty", "start-null", "start-line-null",
                              "start-line-a-string"])
@pytest.mark.parametrize("line", ["absent", None, "2"],
                         ids=["line-absent", "line-null", "line-a-string"])
def test_a_branch_with_neither_loc_nor_line_refuses_the_artifact_naming_it(loc, line):
    def mutate(cov):
        _set(cov["branchMap"]["1"], "loc", loc)
        _set(cov["branchMap"]["1"], "line", line)

    with pytest.raises(ToolError) as raised:
        parse_istanbul(_with(mutate), repo_root="C:\\repo")

    assert str(raised.value).endswith(
        ": src/app.ts: branchMap['1'] has no loc.start.line and no line (every istanbul "
        "reporter writes one; regenerate the artifact with the runner's reporter)"), raised.value


def test_the_lanes_page_quotes_the_branch_refusal():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    with pytest.raises(ToolError) as raised:
        parse_istanbul(_with(lambda cov: cov["branchMap"]["1"].pop("loc")), repo_root="C:\\repo")
    reason = str(raised.value).split(".json: ", 1)[1]

    page = (root / "docs" / "lanes.md").read_text(encoding="utf-8")
    assert f"/repo/.crapkit/cov/js/coverage-final.json: {reason}" in page


# --- every other shape a file's fields arrive in -------------------------------

_APP = "/repo/src/app.ts"


def _istanbul(mutate) -> dict:
    cov = {"path": _APP,
           "fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1, "column": 0}},
                           "loc": {"start": {"line": 1, "column": 0},
                                   "end": {"line": 6, "column": 1}}, "line": 1}},
           "f": {"0": 1},
           "branchMap": {"0": {"loc": {"start": {"line": 2}, "end": {"line": 2}}, "type": "if",
                               "line": 2, "locations": [{"start": {"line": 2}},
                                                        {"start": {"line": 4}}]}},
           "b": {"0": [1, 0]},
           "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}},
                            "1": {"start": {"line": 3}, "end": {"line": 3}},
                            "2": {"start": {"line": 5}, "end": {"line": 5}}},
           "s": {"0": 1, "1": 1, "2": 0}}
    mutate(cov)
    return {_APP: cov}


def _read(mutate):
    return parse_istanbul(json.dumps(_istanbul(mutate)), repo_root="/repo")["src/app.ts"]


_LOUD = {
    "s-null": lambda c: c.update(s=None),
    "s-count-null": lambda c: c["s"].update({"0": None}),
    "b-hits-null": lambda c: c["b"].update({"0": None}),
    "f-count-a-string": lambda c: c["f"].update({"0": "1"}),
    "fnMap-null": lambda c: c.update(fnMap=None),
    "decl-absent-as-istanbul-0x-wrote": lambda c: c["fnMap"]["0"].pop("decl"),
    "decl-start-line-null": lambda c: c["fnMap"]["0"]["decl"]["start"].update(line=None),
    "statement-start-null": lambda c: c["statementMap"]["0"].update(start=None),
    "s-absent": lambda c: c.pop("s"),
    "f-absent": lambda c: c.pop("f"),
}


@pytest.mark.parametrize("shape", list(_LOUD))
def test_a_needed_field_in_the_wrong_shape_refuses_the_artifact(shape):
    with pytest.raises(ToolError, match="istanbul artifact"):
        _read(_LOUD[shape])


@pytest.mark.parametrize("text", ["[]", json.dumps({_APP: None})],
                         ids=["top-level-list", "file-entry-null"])
def test_an_artifact_or_a_file_entry_that_is_not_an_object_refuses_the_artifact(text):
    with pytest.raises(ToolError, match="istanbul artifact"):
        parse_istanbul(text, repo_root="/repo")


def test_a_byte_that_is_not_utf8_refuses_the_artifact(tmp_path):
    from crapkit.coverage_istanbul import parse_istanbul_both_file

    path = tmp_path / "coverage-final.json"
    text = json.dumps(_istanbul(lambda c: c["fnMap"]["0"].update(name="cafe")))
    path.write_bytes(text.encode("ascii").replace(b"cafe", b"caf" + bytes([0xE9])))

    with pytest.raises(ToolError, match="istanbul artifact"):
        parse_istanbul_both_file(path, repo_root="/repo")


_READ_AS = {
    "name-null": (lambda c: c["fnMap"]["0"].update(name=None),
                  ("(anonymous)", 1, 6, True, 2, 1, 3, 2)),
    "statementMap-absent": (lambda c: c.pop("statementMap"), ("f", 1, 6, True, 2, 1, 0, 0)),
    "end-column-null": (lambda c: c["fnMap"]["0"]["loc"]["end"].update(column=None),
                        ("f", 1, 6, True, 2, 1, 3, 2)),
    "path-field-absent": (lambda c: c.pop("path"), ("f", 1, 6, True, 2, 1, 3, 2)),
    "statement-start-empty": (lambda c: c["statementMap"]["0"].update(start={}),
                              ("f", 1, 6, True, 2, 1, 2, 1)),
}


@pytest.mark.parametrize("shape", list(_READ_AS))
def test_an_optional_or_absent_counter_reads_as_istanbul_means_it(shape):
    """A statement with no line is left out, as a branch with no line is. A
    missing counter object is refused: every mapped id needs its counter."""
    mutate, row = _READ_AS[shape]

    assert [tuple(fn) for fn in _read(mutate)] == [row]


def test_an_fnmap_with_no_function_scores_no_function():
    assert _read(lambda c: c.update(fnMap={})) == []


def test_a_non_ascii_path_keys_its_file_as_written():
    art = _istanbul(lambda c: None)
    art["/repo/src/café_世界.ts"] = art.pop(_APP)

    assert list(parse_istanbul(json.dumps(art), repo_root="/repo")) == ["src/café_世界.ts"]


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
