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


# --- a checkout root the reporter spelled another way ---------------------------
#
# The reader stripped the root off each key as literal text. A report made from
# a shell standing in `c:\...`, reached through a junction or symlink, or keyed
# with the `\\?\` prefix named this checkout in a spelling the text strip missed,
# so every key stayed absolute and the lane FAILED, telling the user to point
# the reporter at the checkout it already measured.

import os as _os
from pathlib import Path as _Path

import pytest as _pytest

from crapkit import coverage_istanbul as _adapter
from crapkit.config import Lane as _Lane

from path_spellings import (link_directory as _link, lower_drive as _lower,
                            need_case_insensitive as _need_case_insensitive)


def _tree(root: _Path) -> _Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.ts").write_text("export const a = 1;\n", encoding="utf-8")
    return root.resolve()


def _read_keyed(root: _Path, key: str) -> list[str]:
    body = dict(ARTIFACT["C:\\repo\\src\\app.ts"], path=key)
    artifact = root / "coverage-final.json"
    artifact.write_text(json.dumps({key: body}), encoding="utf-8")
    lane = _Lane(name="unit", command="x", artifact="coverage-final.json", parser="istanbul",
                 scopes=("src",))
    return list(_adapter.read(lane, root, artifact)[0])


def _extended(root: _Path) -> str:
    return "\\\\?\\" + str(root / "src" / "app.ts")


KEY_SPELLINGS = {
    "native": ("", lambda root, tmp: str(root / "src" / "app.ts")),
    "forward-slashes": ("", lambda root, tmp: (root / "src" / "app.ts").as_posix()),
    "relative": ("", lambda root, tmp: "src/app.ts"),
    "relative-backslash": ("", lambda root, tmp: "src\\app.ts"),
    "dot-dot-sibling": ("", lambda root, tmp: str(root.parent / (root.name + "-build") / ".."
                                                   / root.name / "src" / "app.ts")),
    "linked-checkout": ("", lambda root, tmp: str(tmp / "alias" / "src" / "app.ts")),
    "lower-drive": ("windows", lambda root, tmp: _lower(root / "src" / "app.ts")),
    "lower-drive-forward": ("windows", lambda root, tmp: _lower(root / "src" / "app.ts")
                            .replace("\\", "/")),
    "upper-cased": ("windows", lambda root, tmp: str(root / "src" / "app.ts").upper()),
    "extended-length": ("windows", lambda root, tmp: _extended(root)),
    "directory-case": ("case", lambda root, tmp: str(root / "SRC" / "app.ts")),
    "directory-case-forward": ("case", lambda root, tmp: (root / "SRC" / "App.ts").as_posix()),
    "relative-case": ("case", lambda root, tmp: "SRC/APP.ts"),
}


@_pytest.mark.parametrize("which", KEY_SPELLINGS)
def test_every_spelling_of_this_checkout_keys_the_file_git_names(tmp_path, which):
    """A key that keeps this checkout's root as crapkit spells it can still name
    a directory or the file in another case, and a case-insensitive disk opens
    it: the reader keys it in the case the directory lists, as git does."""
    need, spell = KEY_SPELLINGS[which]
    if need == "windows" and _os.name != "nt":
        _pytest.skip("needs Windows path rules")
    if need == "case":
        _need_case_insensitive(tmp_path)
    root = _tree(tmp_path / "repo")
    (tmp_path / "repo-build").mkdir()
    _link(tmp_path / "alias", root)

    assert _read_keyed(root, spell(root, tmp_path)) == ["src/app.ts"]


def test_a_key_from_another_tree_stays_as_the_report_wrote_it(tmp_path):
    root = _tree(tmp_path / "repo")
    elsewhere = _tree(tmp_path / "other")

    assert _read_keyed(root, str(elsewhere / "src" / "app.ts")) == \
        [str(elsewhere / "src" / "app.ts").replace("\\", "/")]


@_pytest.mark.skipif(_os.name == "nt", reason="needs POSIX path rules")
@_pytest.mark.parametrize("absolute", [True, False], ids=["absolute", "relative"])
def test_posix_folds_a_backslash_the_tree_holds_in_a_file_name(tmp_path, absolute):
    """A key cannot say whether its backslash is a Windows separator or a POSIX
    name character, so it separates directories on every OS, and a tracked
    name holding one is unsupported (doctor names it)."""
    root = _tree(tmp_path / "repo")
    (root / "src" / "we\\ird.ts").write_text("export const b = 2;\n", encoding="utf-8")
    key = str(root / "src" / "we\\ird.ts") if absolute else "src/we\\ird.ts"

    assert _read_keyed(root, key) == ["src/we/ird.ts"]
