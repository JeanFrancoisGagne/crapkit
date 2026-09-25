"""Claims: who holds a function, what hides it, and what releases it.

docs/agent-json.md (Claims, `claims`) restated in model_verdict.claim_closes:

- `next-item --claim` holds each item it hands out; a claimed row is hidden
  from every session, and the queue says how many with `skipped_claimed`
  (absent, never 0). `brief --batch` honours the same filter.
- `--claim` on a finished queue holds nothing.
- `verify` releases a claim once the function sits at its ceiling or the
  commit the claim was taken at leaves the history; `claims release` closes
  one by any name form.
"""
from __future__ import annotations

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import repos
from accuracy.kit.settings import process
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

# b2 (CRAP 16) outranks a3 (CRAP 13.125) in next-item's crap-descending queue.
WORLD = (vw.World()
         .with_fn("app", vw.Fn("a1", 1, 2)).with_fn("app", vw.Fn("a3", 6, 6))
         .with_fn("lib", vw.Fn("b1", 2, 4)).with_fn("lib", vw.Fn("b2", 7, 7))
         .with_test(vw.Test("ta")).with_test(vw.Test("tb", lane="b")))
B2 = ("lib/util.py", "b2( x )")


def _measured(repo_templates, top) -> vw.Scenario:
    sc = vw.Scenario(repo_templates.copy(vw.spec(WORLD), top), WORLD)
    assert sc.run("coverage").code == 0
    return sc


def _open(sc) -> set:
    return {(claim["path"], claim["long_name"]) for claim in sc.json("claims")["claims"]}


def _next(sc, *args) -> dict:
    return sc.run("next-item", *args).json()


@pytest.fixture
def claimed(repo_templates, tmp_path) -> vw.Scenario:
    sc = _measured(repo_templates, tmp_path / "repo")
    item = _next(sc, "--claim")["item"]
    assert (item["path"], item["function"]) == B2 and _open(sc) == {B2}
    return sc


@pytest.mark.process
def test_a_claimed_row_is_hidden_from_every_session(claimed):
    """The next call, from the same session or another, hands out a3 and counts b2."""
    payload = _next(claimed)
    assert (payload["item"]["function"], payload["skipped_claimed"]) == ("a3( x )", 1)


@pytest.mark.process
def test_an_unclaimed_queue_carries_no_skipped_claimed(repo_templates, tmp_path):
    sc = _measured(repo_templates, tmp_path / "repo")
    assert "skipped_claimed" not in _next(sc)


@pytest.mark.process
def test_a_batch_skips_a_claimed_function(claimed):
    """brief --batch runs next-item's claim filter over the same ranking."""
    batch = claimed.run("brief", "--batch", "2", "--json").json()
    packed = {(packet["path"], packet["function"]) for packet in batch["packets"]}
    assert B2 not in packed and batch["skipped_claimed"] == 1


@pytest.mark.process
def test_claim_on_a_finished_queue_holds_nothing(make_repo):
    """Every function at or under its ceiling and under the ccn floor: the
    queue is empty and --claim records no claim."""
    world = (vw.World().with_fn("app", vw.Fn("a1", 1, 2)).with_fn("lib", vw.Fn("b1", 2, 4))
             .with_test(vw.Test("ta")).with_test(vw.Test("tb", lane="b")))
    sc = vw.Scenario.build(make_repo, world)
    assert sc.run("coverage").code == 0
    assert _next(sc, "--claim")["empty"] is True and _open(sc) == set()


@pytest.mark.process
@pytest.mark.parametrize("name", ["b2", "b2( x )"])
def test_release_by_hand_takes_any_name_form(claimed, name):
    released = claimed.run("claims", "release", "lib/util.py", name, "--json")
    assert (released.code, released.json()["released"]) == (0, 1) and _open(claimed) == set()


@pytest.mark.process
def test_releasing_a_claim_that_is_not_open_names_what_is(claimed):
    result = claimed.run("claims", "release", "src/app.py", "a3")
    assert result.code == 1 and "b2( x )" in result.stderr, result.stderr


# --- what verify releases ----------------------------------------------------------------------------

def _b2(decisions: int, share: int) -> vw.Fn:
    covered = (share * 2 * decisions) // 4 if decisions else min(share, 1)
    return vw.Fn("b2", decisions, covered)


@pytest.mark.process
@process
@given(decisions=st.integers(0, 7), share=st.integers(0, 4))
def test_verify_releases_a_claim_once_the_function_sits_at_its_ceiling(
        repo_templates, tmp_path_factory, decisions, share):
    """Any rewrite of b2: after verify the claim is open exactly while b2's
    CRAP stays over the ceiling of 6, whatever the verdict."""
    sc = _measured(repo_templates, tmp_path_factory.mktemp("claim") / "repo")
    assert _next(sc, "--claim")["item"]["function"] == "b2( x )"
    fn = _b2(decisions, share)
    sc.set(sc.world.with_fn("lib", fn))
    sc.run("verify")
    closes = model.claim_closes(fn.crap, vw.TARGET, commit_in_history=True)
    assert (B2 in _open(sc)) is not closes, (fn, fn.crap)


@pytest.mark.process
def test_verify_releases_a_claim_whose_commit_left_the_history(claimed):
    """The claim was taken at HEAD; that commit is reset away, so verify
    releases it though b2 is still over its ceiling."""
    claimed.set(claimed.world.with_fn("app", vw.Fn("a1", 1, 2, tag="moved on")))
    claimed.commit("a commit the claim will outlive")
    assert claimed.run("claims", "release", "--all").code == 0
    assert _next(claimed, "--claim")["item"]["function"] == "b2( x )"
    repos.git(claimed.top, "reset", "-q", "--hard", "HEAD~1")
    claimed.write_plan()
    claimed.run("verify")
    assert model.claim_closes(vw.Fn("b2", 7, 7).crap, vw.TARGET, commit_in_history=False)
    assert _open(claimed) == set()


@pytest.mark.process
def test_verify_keeps_a_claim_over_its_ceiling_in_history(claimed):
    claimed.run("verify")
    assert not model.claim_closes(vw.Fn("b2", 7, 7).crap, vw.TARGET, commit_in_history=True)
    assert _open(claimed) == {B2}


@pytest.mark.process
def test_the_claims_list_and_skipped_claimed_count_the_same_rows(claimed):
    """Claim a3 too: two open claims, and the queue says two rows were hidden."""
    assert _next(claimed, "--claim")["item"]["function"] == "a3( x )"
    payload = _next(claimed)
    assert len(_open(claimed)) == payload.get("skipped_claimed") == 2
