"""worklist and `ratchet seed` end with the command to run next.

The README's first run is coverage, worklist, ratchet seed, a commit and
verify. coverage printed `-> next: crapkit worklist`, and worklist and seed
printed no next step, so a user who followed only what crapkit printed stopped
at the risk map with no marks signed and no passing verify.
"""
import json

from cli_inproc_repo import (add_knotty, commit_all, repo, seed_artifacts,  # noqa: F401
                             template_repo)

from crapkit.cli import main
from crapkit.invocation import _self

MARKS = "crapkit-ratchet.tsv"


def run(repo, capsys, *argv) -> tuple[int, list[str]]:
    code = main([*argv, "--repo", str(repo)])
    return code, capsys.readouterr().out.splitlines()


def scored(repo, capsys):
    seed_artifacts(repo)
    assert run(repo, capsys, "coverage", "--reuse-artifacts")[0] == 0
    return repo


def test_worklist_before_any_marks_names_ratchet_seed(repo, capsys):
    code, lines = run(scored(repo, capsys), capsys, "worklist")

    assert code == 0
    assert lines[-2:] == [f"no {MARKS} yet: seed marks each function over its ceiling at "
                          "today's score, and from then on a mark may only fall",
                          f"-> next: {_self()} ratchet seed"], lines


def test_ratchet_seed_names_the_commit_and_then_verify(repo, capsys):
    code, lines = run(scored(repo, capsys), capsys, "ratchet", "seed")

    assert code == 0
    assert lines[-1] == f"-> next: commit {MARKS}, then run `{_self()} verify`", lines


def test_worklist_once_marks_exist_names_next_item(repo, capsys):
    run(scored(repo, capsys), capsys, "ratchet", "seed")

    code, lines = run(repo, capsys, "worklist")

    assert code == 0
    assert lines[-1] == f"-> next: {_self()} next-item", lines
    assert not any(line.startswith(f"no {MARKS}") for line in lines)


def test_worklist_over_an_inventory_run_names_coverage(repo, capsys):
    run(repo, capsys, "inventory")

    code, lines = run(repo, capsys, "worklist")

    assert code == 0
    assert lines[-2:] == ["run 1 is an inventory run (no coverage was measured) and cannot "
                          "serve as a baseline for next-item, ratchet seed or verify",
                          f"-> next: {_self()} coverage"], lines


def test_worklist_json_stays_one_object(repo, capsys):
    code, lines = run(scored(repo, capsys), capsys, "worklist", "--json")

    assert code == 0
    assert json.loads("\n".join(lines))["run_id"] == 1


def test_the_batch_split_prints_before_the_next_step(repo, capsys):
    add_knotty(repo)
    commit_all(repo, "knotty")
    code, lines = run(scored(repo, capsys), capsys, "worklist", "--batches", "2")

    assert code == 0
    assert lines[-3].startswith("batch 1: "), lines
    assert lines[-1] == f"-> next: {_self()} ratchet seed", lines
