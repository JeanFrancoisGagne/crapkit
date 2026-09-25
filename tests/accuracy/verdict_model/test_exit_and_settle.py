"""verify's exit code, its JSON verdict and the run it stores name one verdict.

Every one of the 16 subsets of the four exit-bearing findings (gate, ratchet
regression, new test failure, breached diff coverage) is built on a copy of one
seeded baseline, verified, and judged against hand_exit.tsv: the README's
literal precedence, 6 > 7 > 8 > 9 > 0. The same run's JSON must list exactly
the findings the subset planted, `ok` must agree with the exit, the stored run
must carry the same verdict, and committed plus dirty findings must equal the
total, each finding counted dirty exactly when its file has uncommitted edits.
"""
from __future__ import annotations

import csv
from itertools import product
from pathlib import Path

import pytest

from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

HERE = Path(__file__).resolve().parent
FINDINGS = ("gate", "ratchet", "failures", "diff_uncovered")
JSON_KEYS = {"gate": "gate_violations", "ratchet": "ratchet_regressions",
             "failures": "new_failures", "diff_uncovered": "diff_uncovered"}

BASE = (vw.World(config_extra="diff_uncovered_max = 0")
        .with_fn("app", vw.Fn("gated", 1, 2)).with_fn("app", vw.Fn("steady", 2, 4))
        .with_fn("lib", vw.Fn("marked", 6, 6))
        .with_test(vw.Test("t1")).with_test(vw.Test("t3", lane="b")))
# What each finding plants on the baseline, and the file its finding names.
PLANTS = {
    "gate": lambda w: w.with_fn("app", vw.Fn("gated", 6, 12)),
    "ratchet": lambda w: w.with_fn("lib", vw.Fn("marked", 6, 2)),
    "failures": lambda w: w.with_test(vw.Test("t2", failed=True)),
    "diff_uncovered": lambda w: w.with_fn("app", vw.Fn("fresh", 0, 0)),
}


def _table() -> dict[frozenset, int]:
    with (HERE / "hand_exit.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return {frozenset(name for name in FINDINGS if row[name] == "1"): int(row["exit"])
            for row in rows}


TABLE = _table()
SUBSETS = [frozenset(name for name, on in zip(FINDINGS, bits) if on)
           for bits in product((0, 1), repeat=4)]


def _id(subset: frozenset) -> str:
    return "+".join(name for name in FINDINGS if name in subset) or "none"


def test_the_hand_table_holds_all_16_subsets_and_the_model_reads_it():
    assert sorted(map(sorted, TABLE)) == sorted(map(sorted, SUBSETS))
    assert {subset: model.exit_code(subset) for subset in SUBSETS} == TABLE


@pytest.fixture(scope="module")
def seeded(repo_templates, tmp_path_factory):
    """The baseline: one coverage run, the ratchet seeded from it and committed."""
    built = repo_templates.copy(vw.spec(BASE), tmp_path_factory.mktemp("exit") / "repo")
    scenario = vw.Scenario(built, BASE)
    assert scenario.run("coverage").code == 0
    assert scenario.run("ratchet", "seed").code == 0
    scenario.commit("seed the ratchet")
    return scenario


def _planted(subset: frozenset) -> vw.World:
    world = BASE
    for name in FINDINGS:
        world = PLANTS[name](world) if name in subset else world
    return world


def _expected_counts(subset: frozenset) -> tuple[int, int]:
    """(committed, dirty): the gate and the diff line sit in src/app.py, which
    the plant leaves edited and uncommitted; the regression's lib/util.py and
    the test id are clean."""
    dirty = ("gate" in subset) + ("diff_uncovered" in subset)
    committed = ("ratchet" in subset) + ("failures" in subset)
    return committed, dirty


def _unmarked(world: vw.World) -> int:
    rows = [model.Row(vw.FILES[scope], fn.long_name, start, fn.crap)
            for scope in vw.FILES for fn, start, _ in vw.source(world.functions[scope])[1]]
    marks = {("lib/util.py", "marked( x )"): model.mark_value(BASE.fn("lib", "marked").crap)}
    return model.unmarked_debt(rows, vw.TARGET, marks)


@pytest.mark.process
@pytest.mark.parametrize("subset", SUBSETS, ids=_id)
def test_exit_json_and_stored_run_name_one_verdict(seeded, subset, tmp_path):
    scenario = seeded.copy(tmp_path / "repo")
    world = _planted(subset)
    scenario.set(world)
    scenario.write_artifacts()

    result = scenario.run("verify", "--reuse-artifacts", "--json")
    verdict = result.json()

    assert result.code == TABLE[subset]
    assert {name for name in FINDINGS if verdict[JSON_KEYS[name]]} == set(subset)
    assert verdict["ok"] is (not subset)
    assert scenario.runs()[-1]["kind"] == "verify"
    assert scenario.runs()[-1]["verdict_ok"] == (0 if subset else 1)
    assert (verdict["committed_findings"], verdict["dirty_findings"]) == _expected_counts(subset)
    assert verdict["unmarked_over_target"] == _unmarked(world)
