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
model's set. Every row, and a Hypothesis draw of histories, also runs every
read command before and after the prune: an answer that rests on a kept run
does not move, and a field that lists the run history loses exactly the
pruned runs. read_commands names the doc line behind each; the history
machine (test_history_machine.py) runs the same reads around its prunes.
"""
from __future__ import annotations

import itertools
import json
import os
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


# --- every read command, before and after a prune -----------------------------------------------
# A prune keeps every run a reader picks (README.md:802), so these answers do not move:
# worklist, next-item and rescore read the newest trusted run (README.md:795, 796, 800),
# digest the digest pair (README.md:805) and overrides the override log (README.md:803).
# claims: agent-json.md:244 lets a prune drop a claim older than the oldest kept run;
# these histories take no claim, so the list does not move either.
SAME = (("worklist", "--json"), ("next-item",), ("overrides", "--json"), ("digest",),
        ("rescore", *vw.FILES.values(), "--json"), ("claims", "--json"))


def _same(answer, kept: set):
    return answer


def _holds(answer, key: str) -> bool:
    """A JSON answer that carries `key`; an error object or plain text does not."""
    return isinstance(answer, dict) and key in answer


def _kept_rows(id_field: str):
    """`runs list` and `trend` hold one row per run (README.md:802, 804): a
    pruned run's row leaves, and every kept row stays as it was, so the
    newest trend row and the run `runs list` marks baseline stay too."""
    def expected(answer, kept: set):
        if not _holds(answer, "runs"):
            return answer
        return {**answer, "runs": [row for row in answer["runs"] if row[id_field] in kept]}
    return expected


def _explained(answer, kept: set):
    """README.md:799: explain is a function's score across the stored runs
    plus its mark. Each history loses the pruned runs' entries and a function
    no kept run scored leaves the answer; the mark and the dark lines stay."""
    if not _holds(answer, "functions"):
        return answer
    functions = (_kept_history(fn, kept) for fn in answer["functions"])
    return {**answer, "functions": [fn for fn in functions if fn["history"]]}


def _kept_history(fn: dict, kept: set) -> dict:
    return {**fn, "history": [row for row in fn["history"] if row["run_id"] in kept]}


def _briefed(answer, kept: set):
    """agent-json.md:483: regrowth.history holds a pair for every stored run
    that scored the function, so it loses the pruned runs' pairs, and
    agent-json.md:482 reads `regrown` off that history, so it is worked again
    from the pairs left. Every other field reads the newest trusted run."""
    if not _holds(answer, "regrowth"):
        return answer
    history = [pair for pair in answer["regrowth"]["history"] if pair[0] in kept]
    return {**answer, "regrowth": {"history": history, "regrown": regrown([ccn for _, ccn in history])}}


def _moves(ccns: list[int], sign: int) -> list[int]:
    """The indexes where the ccn moved in `sign`'s direction from the entry before."""
    return [at for at in range(1, len(ccns)) if (ccns[at] - ccns[at - 1]) * sign > 0]


def regrown(ccns: list[int]) -> bool:
    """agent-json.md:482 as written: the ccn fell between two runs and rose
    again at any later point."""
    falls, rises = _moves(ccns, -1), _moves(ccns, 1)
    return bool(falls) and max(rises, default=0) > falls[0]


def read_commands(briefed: dict) -> dict:
    """{argv: what the read must answer after a prune, from its answer before
    and the kept run ids}. `briefed` maps each file to the functions brief
    reads; explain takes their common prefix, which README.md:799's prefix
    match widens to every function of that file any stored run scored."""
    commands = dict.fromkeys(SAME, _same)
    commands[("trend", "--json")] = _kept_rows("run_id")
    commands[("runs", "list", "--json")] = _kept_rows("id")
    for path, names in briefed.items():
        commands[("explain", path, os.path.commonprefix(names), "--json")] = _explained
        commands.update(dict.fromkeys((("brief", path, name, "--json") for name in names), _briefed))
    return commands


def fresh_index(sc) -> None:
    """Ruling V12 (calc-bug verdict-model-8): crapkit's staleness reads start
    `git ls-files` while its own worktree `git diff` rewrites a stat-dirty
    .git/index, and on Windows the ls-files read then fails and names every
    lane stale. A refreshed index leaves that diff nothing to write, so the
    reads around a prune compare answers, not that race; any field that moves
    still fails the check."""
    repos.git(sc.top, "update-index", "-q", "--refresh")


def answers(sc, commands) -> dict:
    """What each read command prints, parsed when it is JSON."""
    return {args: parsed(sc.run(*args).stdout) for args in commands}


def after_prune(before: dict, commands: dict, kept: set) -> dict:
    """What each read must answer after a prune that kept `kept`."""
    return {args: expected(before[args], kept) for args, expected in commands.items()}


def moved(want: dict, got: dict) -> str:
    """One line per field whose answer is not the one wanted, both values in
    full: a str, so the failure report prints it whole."""
    return "\n".join(f"{key} {field}: want={pair[0]!r} got={pair[1]!r}"
                     for key in want if want[key] != got[key]
                     for field, pair in _changed(want[key], got[key]).items())


def _changed(old, new) -> dict:
    old, new = _leaves(old), _leaves(new)
    return {k: (old.get(k), new.get(k)) for k in sorted(old.keys() | new.keys()) if old.get(k) != new.get(k)}


def parsed(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def _leaves(value, where: str = "") -> dict:
    """A JSON value as {path: scalar}."""
    items = _children(value, where)
    if items is None:
        return {where: value}
    return dict(pair for key, child in items for pair in _leaves(child, key).items())


def _children(value, where: str):
    if isinstance(value, dict):
        return [(f"{where}.{key}", child) for key, child in value.items()]
    if isinstance(value, list):
        return [(f"{where}[{number}]", child) for number, child in enumerate(value)]
    return None


READS = read_commands({vw.FILES[scope]: tuple(fn.name for fn in WORLD.functions[scope])
                       for scope in vw.FILES})


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
    fresh_index(sc)
    return answers(sc, READS)


def _pruned(sc) -> set[int]:
    result = sc.run("runs", "prune", "--keep", "1", "--json")
    assert result.code == 0, result.stdout + result.stderr
    return {run["id"] for run in _listed(sc)}


def _check_reads(sc, before: dict, kept: set) -> None:
    """Every read answers after the prune what read_commands allows."""
    want, got = after_prune(before, READS, kept), answers(sc, READS)
    assert got == want, moved(want, got)


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
    this row was built for, and every read answers what read_commands allows."""
    steps, literal = HAND[rule]
    sc = _history(make_repo, steps)
    want, before = expected_keep(sc), _reads(sc)
    assert want == literal
    assert _pruned(sc) == want
    _check_reads(sc, before, want)


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
    assert (kept, want) == ({1, 2, 3, 4}, {1, 2, 3, 4})
    _check_reads(sc, before, want)
    verified = sc.run("verify", "--json")
    assert (verified.code, verified.json()["baseline_run"]) == (6, 1)


# Worked by hand from agent-json.md:482: a fall, then a rise at any later point.
REGROWN = {(): False, (4,): False, (4, 4): False, (4, 3): False, (3, 4): False,
           (4, 3, 3): False, (4, 3, 4): True, (4, 3, 3, 5): True, (2, 5, 4): False,
           (5, 4, 6, 2): True, (5, 3, 4, 2): True, (1, 2, 3, 2, 1): False}


def test_regrown_reads_the_doc_sentence():
    assert {ccns: regrown(list(ccns)) for ccns in REGROWN} == REGROWN


def test_the_allowed_moves_are_the_documented_ones():
    """Each read's answer after a prune that kept runs 2 and 3, worked by hand:
    unchanged, or the runs list, trend rows, explain history and brief
    regrowth less run 1; a function only run 1 scored leaves explain, and
    regrown is read again off the pairs left. An error object stays as it was."""
    rows = {"runs": [{"id": 1}, {"id": 2}, {"id": 3}], "schema": 1}
    explained = {"functions": [{"long_name": "f", "ratchet_mark": 9.0, "history": [{"run_id": 1}, {"run_id": 3}]},
                               {"long_name": "g", "ratchet_mark": None, "history": [{"run_id": 1}]}]}
    briefed = {"ratchet_mark": 9.0, "regrowth": {"history": [[1, 4], [2, 3], [3, 4]], "regrown": True}}
    error = {"error": {"exit": 1}, "schema": 1}
    commands = read_commands({"src/app.py": ("f", "fa")})
    assert set(commands) == {*SAME, ("trend", "--json"), ("runs", "list", "--json"),
                             ("explain", "src/app.py", "f", "--json"),
                             ("brief", "src/app.py", "f", "--json"), ("brief", "src/app.py", "fa", "--json")}
    before = {("worklist", "--json"): error, ("runs", "list", "--json"): rows,
              ("explain", "src/app.py", "f", "--json"): explained,
              ("brief", "src/app.py", "f", "--json"): briefed, ("brief", "src/app.py", "fa", "--json"): ""}
    assert after_prune(before, {args: commands[args] for args in before}, {2, 3}) == {
        ("worklist", "--json"): error, ("runs", "list", "--json"): {"runs": [{"id": 2}, {"id": 3}], "schema": 1},
        ("explain", "src/app.py", "f", "--json"): {"functions": [
            {"long_name": "f", "ratchet_mark": 9.0, "history": [{"run_id": 3}]}]},
        ("brief", "src/app.py", "f", "--json"): {"ratchet_mark": 9.0,
                                                 "regrowth": {"history": [[2, 3], [3, 4]], "regrown": False}},
        ("brief", "src/app.py", "fa", "--json"): ""}


# --- any history --------------------------------------------------------------------------------

@pytest.mark.nightly
@pytest.mark.process
@process
@given(steps=st.lists(st.sampled_from(sorted(set(STEPS) - {"coverage"})), min_size=1, max_size=4))
def test_a_prune_keeps_the_model_s_set_and_changes_no_answer(repo_templates, tmp_path_factory, steps):
    """A history starting with coverage, then any steps: the prune keeps the
    model's set, so every reader's run choice survives, and every read
    command answers what read_commands allows."""
    top = tmp_path_factory.mktemp("prune") / "repo"
    sc = vw.Scenario(repo_templates.copy(vw.spec(WORLD), top), WORLD)
    for name in ("coverage", *steps):
        STEPS[name](sc)
    want, before = expected_keep(sc), _reads(sc)
    assert _pruned(sc) == want
    _check_reads(sc, before, want)


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

