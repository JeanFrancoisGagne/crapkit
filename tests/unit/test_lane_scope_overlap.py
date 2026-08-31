"""A lane's artifact has to be about THIS checkout, and a lane is where that is asked.

Coverage joins on path and nothing else. An artifact whose measured paths reach
none of the scopes its lane claims contributes exactly nothing, and every
function in those scopes then scores `untested` — a confident "N untested,
grade F" assembled entirely out of a tooling mistake. Two worktrees of one
branch reach it quietly: a venv whose editable install points at the other
checkout makes coverage.py measure that tree and report it in absolute paths.

Zero overlap is the whole test — a partial overlap has honest readings, and any
threshold over zero would need tuning per repo — but zero has two readings, and
the measured paths tell them apart. Files outside this checkout can only be
another tree, and that fails the lane. In-tree paths that simply miss the scopes
are also the greenfield shape, a suite importing none of the scoped source yet,
which SHOULD score untested; that one warns and scores on.
"""
import json

import pytest

from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import run_lane

OTHER = "/Users/dev/checkout-b/src/faro/core.py"


def _functions() -> dict:
    return {"functions": {"widget": {
        "start_line": 1, "executed_lines": [1], "missing_lines": [],
        "summary": {"covered_lines": 1, "num_statements": 1,
                    "num_branches": 2, "covered_branches": 2}}}}


def _artifact(tmp_path, *paths: str) -> None:
    report = {"meta": {"branch_coverage": True},
              "files": {path: _functions() for path in paths}}
    (tmp_path / "cov.json").write_text(json.dumps(report), encoding="utf-8")


def _lane(scopes=("src",), path_prefix: str = "") -> Lane:
    return Lane(name="py", command="true", artifact="cov.json", parser="coveragepy",
                scopes=scopes, path_prefix=path_prefix)


def _run(tmp_path, lane: Lane, scope_paths):
    return run_lane(tmp_path, lane, reuse_artifact=True, scope_paths=scope_paths)


def test_an_artifact_that_measured_another_checkout_is_refused(tmp_path):
    _artifact(tmp_path, OTHER)

    with pytest.raises(ToolError) as raised:
        _run(tmp_path, _lane(), {"src": ("src",)})

    assert "none of them under the paths its scopes declare" in str(raised.value)
    assert "describes a different tree" in str(raised.value)


@pytest.mark.parametrize("elsewhere", ["/abs/src/core.py", "../sibling/src/core.py",
                                       "C:/checkout-b/src/core.py"])
def test_every_spelling_of_a_path_outside_the_tree_is_refused(tmp_path, elsewhere):
    """Both parsers rebase a file inside the repo to a repo-relative path, so a
    path that stayed absolute, drive-lettered or climbing is a file elsewhere."""
    _artifact(tmp_path, elsewhere)

    with pytest.raises(ToolError, match="different tree"):
        _run(tmp_path, _lane(), {"src": ("src",)})


def test_the_refusal_quotes_the_paths_the_artifact_does_name(tmp_path):
    """Without them the reader cannot tell a wrong tree from a wrong prefix."""
    _artifact(tmp_path, OTHER)

    with pytest.raises(ToolError) as raised:
        _run(tmp_path, _lane(), {"src": ("src",)})

    assert OTHER in str(raised.value)
    assert "path_prefix" in str(raised.value), "the legitimate remapping knob is named"


def test_the_message_quotes_the_paths_the_config_actually_declares(tmp_path):
    """A scope may declare individual FILES. The message rendered the matcher's
    directory prefix instead, so a scope declaring `src/faro/core.py` was quoted
    as `src/faro/core.py/` — a path in neither the config nor the tree."""
    _artifact(tmp_path, OTHER)

    with pytest.raises(ToolError) as raised:
        _run(tmp_path, _lane(), {"src": ("src/faro/core.py",)})

    assert "declare (src/faro/core.py)" in str(raised.value)


def test_a_scope_declaring_many_paths_does_not_bury_the_advice(tmp_path):
    """Three of them and a count, the way the measured paths beside them are
    sampled; forty declared paths would push the two sentences that say what to
    do off the end of the line."""
    _artifact(tmp_path, OTHER)
    declared = tuple(f"src/f{i}.py" for i in range(8))

    with pytest.raises(ToolError) as raised:
        _run(tmp_path, _lane(), {"src": declared})

    quoted = str(raised.value).split("declare (", 1)[1].split(")", 1)[0]
    assert quoted == "src/f0.py, src/f1.py, src/f2.py and 5 more"


def test_one_file_in_reach_is_enough_to_let_the_artifact_through(tmp_path):
    """A lane measuring part of what it claims is ordinary; only zero is not."""
    _artifact(tmp_path, OTHER, "src/faro/core.py")

    coverage, _, _ = _run(tmp_path, _lane(), {"src": ("src",)})

    assert "src/faro/core.py" in coverage


def test_a_scope_that_declares_individual_files_is_reached_exactly(tmp_path):
    """Scope paths may name files, not only directories, and a prefix test alone
    calls every one of them unreachable."""
    _artifact(tmp_path, "src/faro/core.py")

    coverage, _, _ = _run(tmp_path, _lane(), {"src": ("src/faro/core.py",)})

    assert list(coverage) == ["src/faro/core.py"]


def test_the_prefix_the_lane_declares_is_applied_before_the_question(tmp_path):
    """path_prefix is the remapping the message points at, so an artifact it
    rescues must not be refused on the paths it had before."""
    _artifact(tmp_path, "faro/core.py")

    coverage, _, _ = _run(tmp_path, _lane(path_prefix="src"), {"src": ("src",)})

    assert list(coverage) == ["src/faro/core.py"]


@pytest.mark.parametrize("elsewhere", [OTHER, "../sibling/src/core.py",
                                       "C:/checkout-b/src/core.py"])
def test_a_path_prefix_cannot_hide_an_artifact_from_another_tree(tmp_path, elsewhere):
    """The prefix is glued onto every key the parser yields, absolute ones
    included, so `backend/` + `/Users/dev/checkout-b/...` reads as an ordinary
    relative path. Judged on the prefixed key, a lane that declares path_prefix
    could never be refused, and the wrong-tree grade this check exists to stop
    came back on exactly the monorepo lanes that need the knob."""
    _artifact(tmp_path, elsewhere)

    with pytest.raises(ToolError) as raised:
        _run(tmp_path, _lane(path_prefix="backend"), {"src": ("backend/src",)})

    assert "different tree" in str(raised.value)
    assert elsewhere in str(raised.value), "the path quoted is the one the runner wrote"
    assert "backend/" + elsewhere not in str(raised.value)


def test_in_tree_paths_that_miss_the_scope_warn_rather_than_fail(tmp_path, capsys):
    """The greenfield shape: a suite that imports none of the scoped source yet.
    `untested` is the right answer there, and refusing it would exit 5 on exactly
    the repos that are adopting crapkit."""
    _artifact(tmp_path, "tests/test_core.py")

    coverage, _, _ = _run(tmp_path, _lane(), {"src": ("src",)})

    assert list(coverage) == ["tests/test_core.py"]
    err = capsys.readouterr().err
    assert "will score untested" in err and "tests/test_core.py" in err
    assert "path_prefix" in err


def test_an_artifact_that_measured_nothing_at_all_warns_too(tmp_path, capsys):
    """A coverage.py run that imported no scoped source writes an empty report,
    and that is the same greenfield shape one step further along."""
    _artifact(tmp_path)

    _run(tmp_path, _lane(), {"src": ("src",)})

    assert "measured 0 file(s)" in capsys.readouterr().err


def test_a_reached_scope_says_nothing_at_all(tmp_path, capsys):
    _artifact(tmp_path, "src/faro/core.py")

    _run(tmp_path, _lane(), {"src": ("src",)})

    assert capsys.readouterr().err == ""


def test_a_lane_whose_scopes_declare_no_path_is_not_judged(tmp_path, capsys):
    """Nothing to compare against is not evidence of a mismatch."""
    _artifact(tmp_path, OTHER)

    coverage, _, _ = _run(tmp_path, _lane(scopes=()), {"src": ("src",)})

    assert list(coverage) == [OTHER]
    assert capsys.readouterr().err == ""


def test_a_caller_that_passes_no_scope_paths_is_not_judged(tmp_path):
    _artifact(tmp_path, OTHER)

    coverage, _, _ = run_lane(tmp_path, _lane(), reuse_artifact=True)

    assert list(coverage) == [OTHER]
