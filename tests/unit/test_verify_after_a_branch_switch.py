"""verify, seed and `runs list` read the newest trusted run behind HEAD, not the newest one.

A store keeps every branch's runs. A passing verify on a feature branch is the
newest trusted run, and back on main it is not an ancestor of HEAD. verify took
it anyway, then refused with exit 4 and blamed a rebase or an amend that never
happened; `ratchet seed` signed marks off the feature branch's code, and `runs
list` marked that run as main's baseline.
"""
from cli_inproc_repo import commit_all, git, repo, seed_artifacts, template_repo  # noqa: F401

import json

import pytest

from crapkit.cli import main


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def branch(repo) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def commit_file(repo, rel: str, text: str) -> None:
    (repo / rel).write_text(text, encoding="utf-8")
    commit_all(repo, f"add {rel}")


def switched(repo, capsys, *, main_run: bool = True) -> str:
    """A run on main (unless `main_run` is off), a passing verify on `feature`,
    then back on main with one commit feature never saw. The main branch's name."""
    home = branch(repo)
    seed_artifacts(repo)
    if main_run:
        assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0
    git(repo, "checkout", "-q", "-b", "feature")
    commit_file(repo, "notes-feature.md", "feature\n")
    command = ["verify", "--reuse-artifacts"] if main_run else ["coverage", "--reuse-artifacts"]
    assert run(command, repo, capsys)[0] == 0
    git(repo, "checkout", "-q", home)
    commit_file(repo, "notes-main.md", "main\n")
    return home


def test_verify_back_on_main_measures_against_mains_own_run(repo, capsys):
    switched(repo, capsys)

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], repo, capsys)

    assert code == 0, err
    assert json.loads(out)["baseline_run"] == 1, out
    assert "rewrote history" not in err, err


def test_with_no_run_behind_head_the_refusal_names_the_branch_it_sits_on(repo, capsys):
    """Nothing to measure against on main: the refusal says where the run is and
    what makes one here, instead of blaming a rebase."""
    switched(repo, capsys, main_run=False)

    code, _, err = run(["verify", "--reuse-artifacts"], repo, capsys)

    assert code == 4, err
    assert "rewrote history" not in err, err
    assert "on branch feature" in err, err
    assert "crapkit coverage` on this branch" in err, err


def test_seed_back_on_main_reads_mains_own_run(repo, capsys):
    switched(repo, capsys)

    code, out, err = run(["ratchet", "seed"], repo, capsys)

    assert code == 0, err
    assert "vs run 1 (" in out, out


def test_runs_list_marks_the_run_verify_will_measure_against(repo, capsys):
    switched(repo, capsys)

    code, out, err = run(["runs", "--json"], repo, capsys)

    assert code == 0, err
    marked = [r["id"] for r in json.loads(out)["runs"] if r["baseline"]]
    assert marked == [1], out


@pytest.mark.parametrize("rewrite", ["amend"])
def test_a_rewritten_baseline_commit_still_blames_the_rewrite(repo, capsys, rewrite):
    seed_artifacts(repo)
    assert run(["coverage", "--reuse-artifacts"], repo, capsys)[0] == 0
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "-c", "commit.gpgsign=false", "commit", "-q", "--amend", "-m", "reworded")

    code, _, err = run(["verify", "--reuse-artifacts"], repo, capsys)

    assert code == 4, err
    assert "rewrote history" in err, err
