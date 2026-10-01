"""Outside a commit, with nothing staged, the hook judges every tracked file.

`pre-commit run --all-files` is the form CI runs, through pre-commit/action and
pre-commit.ci. It starts no commit and stages nothing, and the hook read only
the staged diff, so it passed a breach committed from a clone that never ran
`pre-commit install`. git sets GIT_INDEX_FILE for the hooks a commit runs; with
it unset and nothing staged, the hook now judges every tracked file's indexed
content: a function over its ceiling fails unless the marks file in the working
tree marks it. Inside a commit, nothing staged still passes in silence.
"""
import re
from pathlib import Path

import pytest
from cli_inproc_repo import (add_knotty, commit_all, git, istanbul, repo,  # noqa: F401
                             seed_artifacts, template_repo)

from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version


def run(repo: Path, capsys, *argv: str) -> tuple[int, str, str]:
    code = main(["hook-precommit", *argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def committed_breach(repo: Path, monkeypatch) -> Path:
    """knotty( n ) at ccn 8, committed with no gate in the way; nothing staged."""
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    add_knotty(repo)
    commit_all(repo, "a breach from a clone with no hook")
    return repo


def mark_all(repo: Path, *names: str) -> None:
    entries = [RatchetEntry("src/app.ts", name, 99.0) for name in names]
    (repo / "crapkit-ratchet.tsv").write_text(dump_ratchet(entries, stamp=metric_version()),
                                              encoding="utf-8", newline="\n")
    commit_all(repo, "marks")


def test_outside_a_commit_a_committed_breach_fails(committed_breach, capsys):
    code, out, err = run(committed_breach, capsys)

    assert code == 6, out + err
    assert "nothing is staged and no commit is running" in err, err
    assert "tracked function(s) exceed the complexity ceiling of 6:" in out, out
    assert "ccn   8  src/app.ts:20  knotty ( n )" in out, out
    assert "decompose them and commit the split" in out, out


def test_the_whole_tree_refusal_names_the_adoption_route(committed_breach, capsys):
    """Existing debt is what a first hand run meets, and `ratchet seed` is how
    a repo adopts it; the refusal named only decomposition."""
    _, out, _ = run(committed_breach, capsys)

    advice = out.splitlines()[-1]
    assert advice.startswith("decompose them and commit the split"), out
    assert "or record existing debt with `" in advice, out
    assert advice.endswith(" ratchet seed`."), out


def test_the_refusal_names_the_run_seed_reads_before_the_seed(committed_breach, capsys):
    """Seed marks what the stored run scored, so the advice names `coverage`
    first; `ratchet seed` alone left a function committed after the last run
    unmarked, and the hook refused again."""
    _, out, _ = run(committed_breach, capsys)

    advice = out.splitlines()[-1]
    route = r"or record existing debt with `(?P<self>.+) coverage`, then `(?P=self) ratchet seed`\.$"
    assert re.search(route, advice), advice


def test_seed_alone_leaves_a_breach_the_run_never_scored(repo, monkeypatch, capsys):  # noqa: F811
    """The run predates the breach, so seed has nothing to mark, as the
    refusal's order says; a fresh coverage run then lets seed mark it."""
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    add_knotty(repo)
    commit_all(repo, "a breach after the last run")

    assert main(["ratchet", "seed", "--repo", str(repo)]) == 0
    assert run(repo, capsys)[0] == 6

    istanbul(repo, "coverage/unit.json", "src/app.ts",
             {"dispatch": (1, 13, 2), "plain": (13, 20, 1), "knotty": (20, 30, 1)})
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    assert main(["ratchet", "seed", "--repo", str(repo)]) == 0
    commit_all(repo, "marks")
    capsys.readouterr()
    assert run(repo, capsys)[:2] == (0, "")


def test_following_the_seed_route_passes_the_tracked_file_check(committed_breach, capsys):
    seed_artifacts(committed_breach)
    istanbul(committed_breach, "coverage/unit.json", "src/app.ts",
             {"dispatch": (1, 13, 2), "plain": (13, 20, 1), "knotty": (20, 30, 1)})
    assert main(["coverage", "--reuse-artifacts", "--repo", str(committed_breach)]) == 0
    assert main(["ratchet", "seed", "--repo", str(committed_breach)]) == 0
    capsys.readouterr()

    code, out, err = run(committed_breach, capsys)

    assert (code, out) == (0, ""), out + err


def test_marked_functions_pass_the_tracked_file_check(committed_breach, capsys):
    mark_all(committed_breach, "knotty ( n )")

    code, out, err = run(committed_breach, capsys)

    assert (code, out) == (0, ""), out + err
    assert "carry a ratchet mark and were not gated" in err, err


def test_inside_a_commit_nothing_staged_still_passes_in_silence(committed_breach, capsys,
                                                                 monkeypatch):
    monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")

    assert run(committed_breach, capsys) == (0, "", "")


def test_a_base_ref_keeps_the_diff_it_names(committed_breach, capsys):
    """`--base REF` asks for the diff since the fork, which is empty at HEAD."""
    code, out, _ = run(committed_breach, capsys, "--base", "HEAD")

    assert (code, out) == (0, "")


def test_with_something_staged_only_the_staged_change_is_judged(committed_breach, capsys):
    (committed_breach / "notes.md").write_text("n\n", encoding="utf-8")
    git(committed_breach, "add", "notes.md")

    code, out, _ = run(committed_breach, capsys)

    assert (code, out) == (0, "")
