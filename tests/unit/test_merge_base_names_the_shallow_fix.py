"""merge_base says why git found no fork point, and a shallow clone gets the fix.

`verify --base` and `hook-precommit --base` both reach `gitio.merge_base`, and a
CI checkout is a depth-1 clone by default. There git either cannot resolve the
base commit (exit 128) or holds both commits with the fork cut off (exit 1, empty
stderr). The error said `failed in ROOT: ` with nothing after the colon, or git's
own words, and neither named the shallow clone or the fetch that fixes it.
"""
import subprocess
from pathlib import Path

import pytest

from crapkit.cli import main
from crapkit.errors import GitError
from crapkit.gitio import merge_base

SHALLOW = ("; this shallow clone does not hold every commit: set fetch-depth: 0 on the "
           "checkout or run git fetch --unshallow")
TOML = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout.strip()


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def clone(source: Path, target: Path, *args: str) -> Path:
    """`--no-local`, because git ignores --depth on a plain local clone."""
    git(source.parent, "clone", "-q", "--depth", "1", "--no-local", *args, str(source), str(target))
    return target


def _refusal(root: Path, ref: str) -> str:
    with pytest.raises(GitError) as refused:
        merge_base(root, ref)
    return str(refused.value)


@pytest.fixture()
def three(tmp_path: Path) -> tuple[Path, str]:
    """Three commits on main with a crapkit.toml; returns the repo and its root commit."""
    repo = tmp_path / "full"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    first = commit(repo, "one")
    commit(repo, "two")
    commit(repo, "three")
    return repo, first


def test_unrelated_histories_say_there_is_no_merge_base(tmp_path: Path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "one")
    git(tmp_path, "checkout", "-q", "--orphan", "other")
    commit(tmp_path, "two")

    assert _refusal(tmp_path, "main") == f"no merge base between main and HEAD in {tmp_path}"


def test_a_commit_a_depth_one_clone_never_fetched_names_the_fetch_depth_fix(three, tmp_path):
    full, first = three
    shallow = clone(full, tmp_path / "shallow")

    message = _refusal(shallow, first)

    assert message.startswith(f"git merge-base {first} HEAD failed in {shallow}: fatal: ")
    assert message.endswith(SHALLOW)


def test_a_fork_past_the_shallow_boundary_names_the_fetch_depth_fix(tmp_path: Path):
    full = tmp_path / "full"
    full.mkdir()
    git(full, "init", "-q", "-b", "main")
    commit(full, "one")
    git(full, "checkout", "-q", "-b", "feature")
    commit(full, "two")
    git(full, "checkout", "-q", "main")
    commit(full, "three")
    shallow = clone(full, tmp_path / "shallow", "--branch", "feature")
    git(shallow, "fetch", "-q", "--depth", "1", "origin", "main")
    tip = git(shallow, "rev-parse", "FETCH_HEAD")
    assert git(shallow, "cat-file", "-t", tip) == "commit"

    assert _refusal(shallow, tip) == f"no merge base between {tip} and HEAD in {shallow}{SHALLOW}"


def test_a_full_clone_keeps_gits_own_reason_and_no_fetch_advice(three):
    full, _ = three
    missing = "0" * 40

    message = _refusal(full, missing)

    assert message.startswith(f"git merge-base {missing} HEAD failed in {full}: fatal: ")
    assert "shallow" not in message


def test_hook_precommit_base_in_a_depth_one_clone_exits_4_naming_the_fix(three, tmp_path,
                                                                         capsys):
    full, first = three
    shallow = clone(full, tmp_path / "shallow")

    code = main(["hook-precommit", "--base", first, "--repo", str(shallow)])

    err = capsys.readouterr().err
    assert code == 4, err
    assert err.rstrip().endswith(SHALLOW), err


def test_outside_any_repository_the_refusal_keeps_the_merge_base_reason(tmp_path, monkeypatch):
    """The shallow probe fails there too; its error must not replace this one."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    message = _refusal(tmp_path, "main")

    assert message.startswith(f"git merge-base main HEAD failed in {tmp_path}: fatal: ")
