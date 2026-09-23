"""`brief --batch N` hands out what `next-item` would hand out, claims included.

A claim keeps two sessions off one function. `next-item` honoured it and the
batch did not: one session ran `next-item --claim`, an orchestrator then ran
`brief --batch 2`, and the batch carried the claimed function to a second
session anyway.
"""
import json

from hand_scored_repo import make_repo, run, scored, write_run

# Two functions over the ceiling of 6, both uncovered: CRAP = ccn^2 + ccn.
WORST = scored("worst( a )", 1, 20, ccn=12, cov=0.0, crap=156.0, remedy="decompose")
NEXT = scored("next_one( a )", 30, 49, ccn=10, cov=0.0, crap=110.0, remedy="decompose")


def _repo(tmp_path):
    root = make_repo(tmp_path / "repo")
    write_run(root, [WORST, NEXT])
    return root


def test_a_batch_skips_the_function_another_session_claimed(tmp_path, capsys):
    root = _repo(tmp_path)
    code, out, _ = run(root, capsys, "next-item", "--claim")
    assert code == 0 and json.loads(out)["item"]["function"] == "worst( a )"

    code, out, err = run(root, capsys, "brief", "--batch", "2", "--json")

    assert code == 0, err
    batch = json.loads(out)
    assert [p["function"] for p in batch["packets"]] == ["next_one( a )"], \
        "the claimed function went to a second session"
    assert batch["skipped_claimed"] == 1, "next-item's own count, under next-item's own key"


def test_a_batch_nobody_claimed_in_emits_no_skipped_claimed_key(tmp_path, capsys):
    root = _repo(tmp_path)

    code, out, err = run(root, capsys, "brief", "--batch", "2", "--json")

    assert code == 0, err
    batch = json.loads(out)
    assert [p["function"] for p in batch["packets"]] == ["worst( a )", "next_one( a )"]
    assert "skipped_claimed" not in batch, "a store nobody claims in keeps its old payload"


def test_a_released_claim_puts_the_function_back_in_the_batch(tmp_path, capsys):
    root = _repo(tmp_path)
    run(root, capsys, "next-item", "--claim")
    assert run(root, capsys, "claims", "release", "--all")[0] == 0

    code, out, _ = run(root, capsys, "brief", "--batch", "1", "--json")

    assert code == 0
    assert [p["function"] for p in json.loads(out)["packets"]] == ["worst( a )"]
