"""A derived branch counter that came out negative degrades one measurement, never the run.

@vitest/coverage-v8 derives an if/else pair's else count as parent - if, which
underflows on remapped output: 73 counters over 4,166 files in the openclaw
unit-fast artifact, every one at index [1], statements and functions clean.
Refusing the artifact for them left the ratchet unseeded and blocked every
commit in that repo. The numbers below are the real ones.
"""
import json

import pytest

from crapkit.errors import ToolError
from coverage_readers import parse_istanbul

POLICY_API = "/repo/extensions/openai/provider-policy-api.ts"


def artifact(branches, functions=None, statements=None):
    return json.dumps({POLICY_API: {
        "path": POLICY_API,
        "fnMap": {"0": {"name": "resolvePolicy",
                        "decl": {"start": {"line": 1}},
                        "loc": {"start": {"line": 1}, "end": {"line": 40}}}},
        "f": functions or {"0": 12},
        "branchMap": {
            "6": {"loc": {"start": {"line": 6}},
                  "locations": [{"start": {"line": 6}}, {"start": {"line": 7}}]},
            "13": {"loc": {"start": {"line": 13}},
                   "locations": [{"start": {"line": 13}}, {"start": {"line": 14}}]},
        },
        "b": branches,
        "statementMap": {"0": {"start": {"line": 2}}},
        "s": statements or {"0": 12},
    }})


def only_function(text):
    per_file = parse_istanbul(text, repo_root="/repo")
    return per_file["extensions/openai/provider-policy-api.ts"][0]


def test_negative_derived_branch_count_does_not_refuse_the_artifact():
    fn = only_function(artifact({"6": [77, -101], "13": [4, -81]}))
    assert fn.branches_total == 4


def test_a_clamped_branch_reads_uncovered_never_negative():
    fn = only_function(artifact({"6": [77, -101], "13": [4, -81]}))
    assert fn.branches_covered == 2
    assert fn.coverage == 0.5


def test_the_run_names_how_many_counters_it_clamped(capsys):
    only_function(artifact({"6": [77, -101], "13": [4, -81]}))
    err = capsys.readouterr().err
    assert "2" in err
    assert "extensions/openai/provider-policy-api.ts" in err
    assert "clamped" in err


def test_a_clean_artifact_says_nothing(capsys):
    only_function(artifact({"6": [77, 3], "13": [4, 1]}))
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("group,payload", [
    ("f", {"0": -3}),
    ("s", {"0": -3}),
])
def test_a_negative_measured_count_is_still_fatal(group, payload):
    """Only branch index counts are derived. A negative f or s is corruption."""
    kwargs = {"functions" if group == "f" else "statements": payload}
    with pytest.raises(ToolError, match="nonnegative integer count"):
        only_function(artifact({"6": [77, 3], "13": [4, 1]}, **kwargs))


def test_a_float_that_is_a_whole_negative_clamps_like_an_int():
    """istanbul writes counters through JSON, where 3 and 3.0 are the same token."""
    fn = only_function(artifact({"6": [77, -101.0], "13": [4, -81]}))
    assert fn.branches_total == 4
    assert fn.branches_covered == 2


def many_files_artifact(count):
    """One artifact holding `count` files, every one with a clamped branch."""
    one = json.loads(artifact({"6": [77, -101], "13": [4, -81]}))[POLICY_API]
    files = {}
    for index in range(count):
        path = f"/repo/src/file{index}.ts"
        files[path] = {**one, "path": path}
    return json.dumps(files)


def test_more_clamped_files_than_it_names_are_counted(capsys):
    """Four files, three named: the rest is a count, never a wall of paths."""
    parse_istanbul(many_files_artifact(4), repo_root="/repo")

    err = capsys.readouterr().err
    assert "8 negative derived branch count(s) in 4 file(s)" in err
    assert "... and 1 more" in err
