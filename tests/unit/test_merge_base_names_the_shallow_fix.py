"""merge_base says why git found no fork point, and a shallow clone gets the fix.

`verify --base` and `hook-precommit --base` both reach `gitio.merge_base`, and a
CI checkout is a depth-1 clone by default. There git either cannot resolve the
base commit (exit 128) or holds both commits with the fork cut off (exit 1, empty
stderr). The error said `failed in ROOT: ` with nothing after the colon, or git's
own words, and neither named the shallow clone or the fetch that fixes it.

The repos are test_gitio_shallow's: `full` holds two commits on main, and
`shallow` is its depth-1 clone, which holds only the second.
"""
from pathlib import Path

import pytest

from crapkit.cli import main
from crapkit.errors import GitError
from crapkit.gitio import merge_base
from test_gitio_shallow import commit, full, git, shallow

SHALLOW = ("; this shallow clone does not hold every commit: set fetch-depth: 0 on the "
           "checkout or run git fetch --unshallow")
TOML = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def _refusal(root: Path, ref: str) -> str:
    with pytest.raises(GitError) as refused:
        merge_base(root, ref)
    return str(refused.value)


def _first(repo: Path) -> str:
    return git(repo, "rev-list", "--max-parents=0", "HEAD")


def test_a_commit_a_depth_one_clone_never_fetched_names_the_fetch_depth_fix(full, shallow):
    first = _first(full)

    message = _refusal(shallow, first)

    assert message.startswith(f"git merge-base {first} HEAD failed in {shallow}: fatal: ")
    assert message.endswith(SHALLOW)


def test_a_fork_past_the_shallow_boundary_names_the_fetch_depth_fix(full, shallow):
    """feature forks from the first commit; the clone fetches its tip at depth 1,
    so it holds both tips and neither parent."""
    git(full, "checkout", "-q", "-b", "feature", _first(full))
    commit(full, "three")
    main_tip = git(shallow, "rev-parse", "HEAD")
    git(shallow, "fetch", "-q", "--depth", "1", "origin", "feature")
    git(shallow, "checkout", "-q", "FETCH_HEAD")

    assert _refusal(shallow, main_tip) == (
        f"no merge base between {main_tip} and HEAD in {shallow}{SHALLOW}")


def test_a_full_clone_keeps_gits_own_reason_and_no_fetch_advice(full):
    missing = "0" * 40

    message = _refusal(full, missing)

    assert message.startswith(f"git merge-base {missing} HEAD failed in {full}: fatal: ")
    assert "shallow" not in message


def test_hook_precommit_base_in_a_depth_one_clone_exits_4_naming_the_fix(full, shallow, capsys):
    (shallow / "crapkit.toml").write_text(TOML, encoding="utf-8")

    code = main(["hook-precommit", "--base", _first(full), "--repo", str(shallow)])

    err = capsys.readouterr().err
    assert code == 4, err
    assert err.rstrip().endswith(SHALLOW), err


def test_outside_any_repository_the_refusal_keeps_the_merge_base_reason(tmp_path, monkeypatch):
    """The shallow probe fails there too; its error must not replace this one."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    message = _refusal(tmp_path, "main")

    assert message.startswith(f"git merge-base main HEAD failed in {tmp_path}: fatal: ")
