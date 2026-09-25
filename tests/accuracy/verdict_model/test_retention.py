"""`runs prune --keep N`: which runs it keeps, and that no reader notices.

README.md:802: --keep is a floor on the newest trusted runs, not a cap; the
digest pair (README.md:805: the two newest runs with identical lane sets),
every passing verify, every run an override names (docs/ratchet.md:813) and
the newest non-hook run are kept too. model_verdict.keep_set adds the runs the
taint rule names (the baseline, the newest trusted run it passed over and the
failed verifies after the baseline), which README.md:802 does not list:
ruling V1.

Each hand row builds the shortest history in which one rule alone keeps a
run, and checks the store after `runs prune --keep 1` holds exactly the
model's set. Every row, and a Hypothesis draw of histories, also checks that
the read commands answer after the prune as they did before it.
"""
from __future__ import annotations

import itertools
import shutil
import sqlite3

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import drive, repos, rulings
from accuracy.kit.settings import process
from accuracy.verdict_model import cadence
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

WORLD = (vw.World()
         .with_fn("app", vw.Fn("a1", 1, 2)).with_fn("app", vw.Fn("a3", 6, 6))
         .with_fn("lib", vw.Fn("b1", 2, 4)).with_fn("lib", vw.Fn("b2", 7, 7))
         .with_test(vw.Test("ta")).with_test(vw.Test("tb", lane="b")))
WORSE = vw.Fn("a3", 7, 0)          # ccn 8, CRAP 72: a touched gate violation
KINDS = {"coverage": model.COVERAGE, "verify": model.VERIFY, "partial": model.PARTIAL,
         "inventory": model.INVENTORY, "hook": model.HOOK}
# The reads whose answer rests on a run choice. `runs list`, `trend` and
# `explain` also list the history, which a prune shortens by design; of those
# the baseline mark and the newest trend row are compared instead.
READS = (("worklist", "--json"), ("next-item",), ("overrides", "--json"))


# --- steps a history is made of ----------------------------------------------------------------

def coverage(sc):
    assert sc.run("coverage").code == 0


def one_lane(sc):
    assert sc.run("coverage", "--lane", "a").code == 0


def inventory(sc):
    assert sc.run("inventory").code == 0


# The verify and grant steps set up the tree their name says and run the
# command; a drawn history can leave them nothing to refuse or grant, and the
# prune checks read what the store holds, so they assert no exit code.
def verify_pass(sc):
    sc.set(sc.world.with_fn("app", vw.Fn("a3", 6, 6)))
    sc.run("verify")


def verify_fail(sc):
    sc.set(sc.world.with_fn("app", WORSE))
    sc.run("verify")


def override(sc):
    sc.set(sc.world.with_fn("app", WORSE))
    sc.run("verify", "--override", "reviewed debt")


def hook_grant(sc):
    sc.set(sc.world.with_fn("lib", vw.Fn("b1", 7, 0)))
    repos.git(sc.top, "add", "-A")
    drive.Driver(sc.root, date_now=sc.date + vw.DAY,
                 env={"CRAPKIT_OVERRIDE_REASON": "reviewed"}).run("hook-precommit")


STEPS = {step.__name__: step for step in (coverage, one_lane, inventory, verify_pass, verify_fail,
                                           override, hook_grant)}


# --- the model's inputs, read off the store ----------------------------------------------------

def _listed(sc) -> list[dict]:
    return sc.json("runs", "list")["runs"]


def _runs(listed: list[dict]) -> list[model.Run]:
    return [model.Run(run["id"], KINDS[run["kind"]], run["commit"],
                      None if run["verdict_ok"] is None else bool(run["verdict_ok"]))
            for run in listed]


def digest_pair(listed: list[dict]) -> set[int]:
    """README.md:805: the two newest trusted runs with identical lane sets."""
    trusted = [run for run in reversed(listed) if model.trusted(_runs([run])[0])]
    pair = next(filter(_same_lanes, itertools.combinations(trusted, 2)), ())
    return {run["id"] for run in pair}


def _same_lanes(pair) -> bool:
    return set(pair[0]["lanes"]) == set(pair[1]["lanes"])


def _override_runs(sc) -> set[int]:
    return {row["run_id"] for row in sc.json("overrides")["overrides"]}


def expected_keep(sc, keep: int = 1) -> set[int]:
    listed = _listed(sc)
    return model.keep_set(_runs(listed), keep, _override_runs(sc), digest_pair(listed))


def _reads(sc) -> dict:
    """What each read command prints, the baseline `runs list` marks and the
    newest trend row: a prune must leave every one alone."""
    answers = {args: sc.run(*args).stdout for args in READS}
    answers["brief"] = _brief(sc)
    answers["baseline"] = [run["id"] for run in _listed(sc) if run["baseline"]]
    answers["newest trend row"] = sc.json("trend")["runs"][-1:]
    return answers


def _brief(sc) -> dict:
    """brief's packet for b2, less regrowth.history: one (run, ccn) pair per
    run that scored it, a history list a prune shortens by design."""
    packet = sc.run("brief", "lib/util.py", "b2", "--json").json()
    packet["regrowth"].pop("history")
    return packet


def _pruned(sc) -> set[int]:
    result = sc.run("runs", "prune", "--keep", "1", "--json")
    assert result.code == 0, result.stdout + result.stderr
    return {run["id"] for run in _listed(sc)}


def _history(make_repo, steps) -> vw.Scenario:
    sc = vw.Scenario.build(make_repo, WORLD)
    for name in steps:
        STEPS[name](sc)
    return sc


# One row per keep rule: a history, and the set `runs prune --keep 1` must leave, worked
# by hand from README.md:802. In each, the rule named is what keeps its oldest kept run.
HAND = {
    "newest trusted": (("coverage", "coverage", "coverage"), {2, 3}),
    "digest pair": (("coverage", "coverage", "one_lane", "one_lane"), {1, 2, 4}),
    "passing verify": (("coverage", "verify_pass", "coverage", "coverage"), {2, 3, 4}),
    "newest non-hook": (("coverage", "coverage", "coverage", "inventory"), {2, 3, 4}),
    "hook override": (("coverage", "hook_grant", "coverage", "coverage"), {2, 3, 4}),
}


@pytest.mark.process
@pytest.mark.parametrize("rule", cadence.tiered(sorted(HAND), push={"passing verify"}))
def test_each_keep_rule_keeps_its_run(make_repo, rule):
    """The store after the prune holds exactly the model's set, the literal set
    this row was built for, and every read answers as before."""
    steps, literal = HAND[rule]
    sc = _history(make_repo, steps)
    want, before = expected_keep(sc), _reads(sc)
    assert want == literal
    assert _pruned(sc) == want
    assert _reads(sc) == before


@pytest.mark.process
def test_the_taint_rule_s_runs_survive_a_prune(make_repo):
    """[coverage, failed verify, coverage, coverage]: verify's baseline is run 1
    in front of the failure. A prune that dropped runs 1 and 2 would move the
    baseline past the gate finding the taint rule protects; crapkit keeps them,
    which README.md:802's list does not say (ruling V1)."""
    sc = _history(make_repo, ("coverage", "verify_fail", "coverage", "coverage"))
    runs = _runs(_listed(sc))
    assert model.baseline(runs).id == 1 and model.taint_runs(runs) == {1, 2, 4}
    want, before = expected_keep(sc), _reads(sc)
    kept = _pruned(sc)
    rulings.pin_ruling("V1", crapkit="kept" if model.taint_runs(runs) <= kept else "dropped",
                       oracle="not listed")
    assert (kept, want) == ({1, 2, 3, 4}, {1, 2, 3, 4}) and _reads(sc) == before
    verified = sc.run("verify", "--json")
    assert (verified.code, verified.json()["baseline_run"]) == (6, 1)


# --- any history --------------------------------------------------------------------------------

@pytest.mark.nightly
@pytest.mark.process
@process
@given(steps=st.lists(st.sampled_from(sorted(set(STEPS) - {"coverage"})), min_size=1, max_size=4))
def test_a_prune_keeps_the_model_s_set_and_changes_no_answer(repo_templates, tmp_path_factory, steps):
    """A history starting with coverage, then any steps: the prune keeps the
    model's set, so every reader's run choice survives, and every read
    command answers as it did before."""
    top = tmp_path_factory.mktemp("prune") / "repo"
    sc = vw.Scenario(repo_templates.copy(vw.spec(WORLD), top), WORLD)
    for name in ("coverage", *steps):
        STEPS[name](sc)
    want, before = expected_keep(sc), _reads(sc)
    assert _pruned(sc) == want
    assert _reads(sc) == before


# --- a store holding a run written before same-line order was recorded -------------------------

CALLBACKS = {"crapkit.toml": ('[crapkit]\ntarget = 1\n\n[[scope]]\nname = "web"\npaths = ["web"]\n'
                              'languages = ["typescript"]\ncoverage_optional = true\n'),
             "web/app.ts": ("export const f = (a: number) => [a].map(x => x > 1 ? 1 : x > 0 ? 2 : 3)"
                            ".filter(y => y > 2 || y < 0);\n")}


def _legacy_first_run(root) -> None:
    connection = sqlite3.connect(root / ".crapkit" / "crap.sqlite")
    with connection:
        connection.execute("UPDATE functions SET occurrence = 0 WHERE run_id = 1")
    connection.close()


@pytest.mark.process
def test_a_legacy_key_seed_answers_as_before_the_prune(make_repo, tmp_path):
    """Run 1 lost its same-line order (CONTEXT.md, Legacy run); runs 2 to 4
    are current. `ratchet seed` gives the same exit and the same marks on a
    copy pruned to --keep 1 as on one left alone."""
    built = make_repo(repos.Spec(steps=(repos.Commit(files=CALLBACKS, message="seed"),)))
    driver = drive.Driver(built.root)
    for _ in range(4):
        assert driver.run("coverage").code == 0
    _legacy_first_run(built.root)
    answers = []
    for prune in (False, True):
        copy = tmp_path / f"copy-{prune}"
        shutil.copytree(built.top, copy)
        copied = drive.Driver(copy)
        if prune:
            assert copied.run("runs", "prune", "--keep", "1").code == 0
        seeded = copied.run("ratchet", "seed")
        marks = copy / "crapkit-ratchet.tsv"
        answers.append((seeded.code, marks.read_bytes() if marks.exists() else None))
    assert answers[0] == answers[1]

