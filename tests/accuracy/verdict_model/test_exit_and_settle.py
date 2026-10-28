"""verify's exit code, its JSON verdict and the run it stores name one verdict.

hand_exit.tsv holds every subset of the six exit-bearing findings: a file a
scope takes whose name is not UTF-8, a gate violation, a changed file no reader
could read, a ratchet regression, a new test failure and a breached diff
coverage. Each subset is built on a copy of one seeded baseline, verified, and
judged against the table: the README's literal precedence, 3 > 6 > 7 > 8 > 9 > 0.

Below exit 3 the same run's JSON must list exactly the findings the subset
planted, `ok` must agree with the exit, the stored run must carry the same
verdict, and committed plus dirty findings must equal the total, each finding
counted dirty exactly when its file has uncommitted edits. A name that is not
UTF-8 stops verify before any lane runs (docs/agent-json.md, "A scoped file
whose name is not UTF-8"): the JSON lists that name and nothing else, and no
run is stored.
"""
from __future__ import annotations

import csv
from itertools import product
import os
from pathlib import Path

import pytest

import git_env
import hang_guard
from accuracy.kit import repos
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

HERE = Path(__file__).resolve().parent
# The four 0.8.1 kinds, each planted by editing the World.
FINDINGS = ("gate", "ratchet", "failures", "diff_uncovered")
# The two kinds 0.9.0 added to the table, each planted on the tree beside the World.
ADDED = ("unreadable_name", "unread")
# hand_exit.tsv's finding columns, in its order.
COLUMNS = (*ADDED, *FINDINGS)
# The findings item kind each finding lists under in verify --json.
KINDS = {"unreadable_name": "unreadable_name", "unread": "unread_file", "gate": "gate_violation",
         "ratchet": "ratchet_regression", "failures": "new_failure",
         "diff_uncovered": "diff_uncovered"}

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
# A def cut off inside its signature: the Python reader reaches no body for
# it, so the file is not scored and every gate refuses it as unread.
UNREAD_PATH = "src/knot.py"
UNREAD_SOURCE = "def knot(x, y=(),\n"
# A name the app scope takes, in bytes that are not UTF-8, and how verify --json shows it.
NAME = b"src/caf\xe9.py"
SHOWN = "src/caf\\xe9.py"


def _table() -> dict[frozenset, int]:
    with (HERE / "hand_exit.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return {frozenset(name for name in COLUMNS if row[name] == "1"): int(row["exit"])
            for row in rows}


TABLE = _table()
SUBSETS = [frozenset(name for name, on in zip(FINDINGS, bits) if on)
           for bits in product((0, 1), repeat=4)]
# Each of the 16 beside an unread file, then each of those 32 beside an unreadable name.
UNREAD = [subset | {"unread"} for subset in SUBSETS]
NAMED = [subset | {"unreadable_name"} for subset in SUBSETS + UNREAD]


def _id(subset: frozenset) -> str:
    return "+".join(name for name in COLUMNS if name in subset) or "none"


def test_the_hand_table_holds_all_16_subsets_and_the_model_reads_it():
    """The table holds the 16 subsets of the four 0.8.1 kinds, the 16 again
    beside an unread file and those 32 beside an unreadable name, and nothing
    else, so the drive below verifies every row."""
    driven = SUBSETS + UNREAD + NAMED
    assert sorted(map(sorted, TABLE)) == sorted(map(sorted, driven))
    assert {subset: model.exit_code(subset) for subset in driven} == TABLE


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


def _plant_unread(scenario: vw.Scenario) -> None:
    """The unread file, staged and not committed."""
    (scenario.root / UNREAD_PATH).write_bytes(UNREAD_SOURCE.encode("utf-8"))
    repos.git(scenario.root, "add", "--", UNREAD_PATH)


def _plant_name(scenario: vw.Scenario) -> None:
    """The name staged through `git update-index --index-info`, the one route
    that puts it in git on every OS (a Windows argv cannot carry it), and kept
    off the disk, which NTFS and APFS refuse it: git reads it added and
    deleted, so it is dirty on every OS."""
    blob = repos.git(scenario.root, "rev-parse", "HEAD:src/app.py").strip().encode("ascii")
    done = hang_guard.run(["git", "update-index", "--add", "-z", "--index-info"], cwd=scenario.root,
                          input=b"100644 " + blob + b"\t" + NAME + b"\0",
                          env=git_env.without_repo_env(os.environ))
    assert done.returncode == 0, done.stderr


TREE_PLANTS = {"unreadable_name": _plant_name, "unread": _plant_unread}


def _verified(seeded: vw.Scenario, subset: frozenset, tmp_path: Path):
    """The subset planted on a copy of the baseline, and verify's result there."""
    scenario = seeded.copy(tmp_path / "repo")
    world = _planted(subset)
    scenario.set(world)
    scenario.write_artifacts()
    for name in ADDED:
        if name in subset:
            TREE_PLANTS[name](scenario)
    return scenario, world, scenario.run("verify", "--reuse-artifacts", "--json")


def _expected_counts(subset: frozenset) -> tuple[int, int]:
    """(committed, dirty): the gate and the diff line sit in src/app.py, which
    the plant leaves edited and uncommitted, and the unread file is staged, not
    committed; the regression's lib/util.py and the test id are clean."""
    dirty = ("gate" in subset) + ("diff_uncovered" in subset) + ("unread" in subset)
    committed = ("ratchet" in subset) + ("failures" in subset)
    return committed, dirty


def _kinds(payload: dict) -> set[str]:
    """The kinds verify --json lists a finding of. A crapkit before 0.9.0,
    which a retro replay runs, printed one list per kind instead of `findings`."""
    if "findings" not in payload:
        lists = {"gate_violation": payload["gate_violations"],
                 "ratchet_regression": payload["ratchet_regressions"],
                 "new_failure": payload["new_failures"], "diff_uncovered": payload["diff_uncovered"]}
        return {kind for kind, entries in lists.items() if entries}
    return {item["kind"] for item in payload["findings"]}


def _unmarked(world: vw.World) -> int:
    rows = [model.Row(vw.FILES[scope], fn.long_name, start, fn.crap)
            for scope in vw.FILES for fn, start, _ in vw.source(world.functions[scope])[1]]
    marks = {("lib/util.py", "marked( x )"): model.mark_value(BASE.fn("lib", "marked").crap)}
    return model.unmarked_debt(rows, vw.TARGET, marks)


def _one_verdict(seeded: vw.Scenario, subset: frozenset, tmp_path: Path) -> None:
    scenario, world, result = _verified(seeded, subset, tmp_path)
    verdict = result.json()

    assert result.code == TABLE[subset]
    assert {name for name in COLUMNS if KINDS[name] in _kinds(verdict)} == set(subset)
    assert verdict["ok"] is (not subset)
    assert scenario.runs()[-1]["kind"] == "verify"
    assert scenario.runs()[-1]["verdict_ok"] == (0 if subset else 1)
    assert (verdict["committed_findings"], verdict["dirty_findings"]) == _expected_counts(subset)
    assert verdict["unmarked_over_target"] == _unmarked(world)


@pytest.mark.process
@pytest.mark.parametrize("subset", SUBSETS, ids=_id)
def test_exit_json_and_stored_run_name_one_verdict(seeded, subset, tmp_path):
    _one_verdict(seeded, subset, tmp_path)


@pytest.mark.process
@pytest.mark.parametrize("subset", UNREAD, ids=_id)
def test_an_unread_file_beside_each_subset_names_one_verdict(seeded, subset, tmp_path):
    """README#exit-codes: 6 also refuses a changed file no reader could read,
    beside each subset of the four 0.8.1 kinds, and the run is stored failed."""
    _one_verdict(seeded, subset, tmp_path)


@pytest.mark.process
@pytest.mark.parametrize("subset", NAMED, ids=_id)
def test_an_unreadable_name_stops_verify_at_3_and_stores_no_run(seeded, subset, tmp_path):
    """Whatever else the tree holds, verify stops on the name before any lane
    runs: the name is the one finding, and the store holds only the baseline's runs."""
    scenario, _, result = _verified(seeded, subset, tmp_path)
    verdict = result.json()

    assert result.code == TABLE[subset]
    assert [(item["kind"], item["path"], item["dirty"]) for item in verdict["findings"]] == [
        ("unreadable_name", SHOWN, True)]
    assert (verdict["ok"], verdict["run_id"]) == (False, None)
    assert scenario.runs() == seeded.runs()
    assert (verdict["committed_findings"], verdict["dirty_findings"]) == (0, 1)
