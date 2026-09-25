"""Which run a verdict is measured against, and the portable baseline that carries it.

- `--baseline ID` steps past the taint rule and nothing else: a failed verify,
  a hook run, a partial run or an inventory run is refused with the reason and
  the trusted runs (CONTEXT.md, Named baseline; agent-json.md, the refusal
  lines; model_verdict.named_baseline).
- `verify --emit-baseline` writes the run as a portable record
  (docs/portable-records.md): its rows read back to the store's, CRAP to the
  bit, so a mark compared against either reads the same.
- `verify --baseline-tsv` on a clone with no store gives the verdict the store
  baseline gives (README, the portable baseline), and a 16-column file written
  before rows carried `occurrence` reads as occurrence 0 (portable-records.md:
  16/17-column scored exports remain readable).
- README exit 8: failures the baseline already had do not count. The portable
  record carries no test failures, so under `--baseline-tsv` they count as
  new: ruling V8, an open defect.
"""
from __future__ import annotations

import json
import shutil

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import rulings
from accuracy.kit.settings import process
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

WORLD = (vw.World()
         .with_fn("app", vw.Fn("a1", 1, 2)).with_fn("app", vw.Fn("a3", 6, 6))
         .with_fn("lib", vw.Fn("b1", 2, 4)).with_fn("lib", vw.Fn("b2", 7, 7))
         .with_test(vw.Test("ta")).with_test(vw.Test("tb", lane="b")))
WORSE = vw.Fn("a3", 7, 0)
KINDS = {"coverage": model.COVERAGE, "verify": model.VERIFY, "partial": model.PARTIAL,
         "inventory": model.INVENTORY, "hook": model.HOOK}


def _measured(make_repo, world: vw.World = WORLD) -> vw.Scenario:
    sc = vw.Scenario.build(make_repo, world)
    assert sc.run("coverage").code == 0
    return sc


def _runs(sc) -> list[model.Run]:
    return [model.Run(run["id"], KINDS[run["kind"]], run["commit"],
                      None if run["verdict_ok"] is None else bool(run["verdict_ok"]))
            for run in sc.json("runs", "list")["runs"]]


# --- --baseline ID ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def every_kind(repo_templates, tmp_path_factory):
    """Runs 1 coverage, 2 failed verify, 3 inventory, 4 partial (one lane),
    5 coverage, 6 hook (the pre-commit override), 7 coverage."""
    top = tmp_path_factory.mktemp("kinds") / "repo"
    sc = vw.Scenario(repo_templates.copy(vw.spec(WORLD), top), WORLD)
    assert sc.run("coverage").code == 0
    sc.set(sc.world.with_fn("app", WORSE))
    assert sc.run("verify").code == 6
    assert sc.run("inventory").code == 0
    assert sc.run("coverage", "--lane", "a").code == 0
    assert sc.run("coverage").code == 0
    vw.repos.git(sc.top, "add", "-A")
    grant = vw.drive.Driver(sc.root, env={"CRAPKIT_OVERRIDE_REASON": "reviewed"})
    assert grant.run("hook-precommit").code == 0
    assert sc.run("coverage").code == 0
    return sc


@pytest.mark.process
@pytest.mark.parametrize("wanted", [2, 3, 4, 6, 99])
def test_a_named_baseline_that_cannot_serve_is_refused_with_its_reason(every_kind, wanted):
    runs = _runs(every_kind)
    assert [run.kind for run in runs] == ["coverage", "verify", "inventory", "partial", "coverage",
                                          "hook", "coverage"]
    found, reason = model.named_baseline(runs, wanted)
    trusted = [run.id for run in runs if model.trusted(run)]
    result = every_kind.run("verify", "--baseline", str(wanted))
    assert found is None and result.code != 0
    assert reason in result.stderr, result.stderr
    assert f"trusted runs: {', '.join(map(str, trusted))}" in result.stderr, result.stderr
    newest = f"pass `--baseline {trusted[-1]}` for the newest"
    assert (newest in result.stderr) is (wanted != 99), result.stderr


@pytest.mark.process
def test_a_named_baseline_steps_past_the_taint_rule(every_kind):
    """Run 2 failed and nothing has passed since, so plain verify measures
    against run 1; --baseline 5 names a coverage run and is accepted."""
    runs = _runs(every_kind)
    assert model.baseline(runs).id == 1 and model.named_baseline(runs, 5) == (runs[4], None)
    plain = every_kind.run("verify", "--no-tighten", "--json").json()
    named = every_kind.run("verify", "--baseline", "5", "--no-tighten", "--json").json()
    assert (plain["baseline_run"], named["baseline_run"]) == (1, 5)


# --- the emitted record ---------------------------------------------------------------------------

def read_record(text: str) -> tuple[str, list[dict]]:
    """A portable scored record read as docs/portable-records.md says: split at
    LF, drop one CR, the comment line, the header, then one row per line (an
    encoded row is a JSON array)."""
    lines = _physical_rows(text)
    header, *body = [line for line in lines if not _comment(line)]
    rows = [dict(zip(header.split("\t"), _fields(line))) for line in body]
    return next(filter(_comment, lines), ""), rows


def _physical_rows(text: str) -> list[str]:
    return [line.removesuffix("\r") for line in text.split("\n") if line.strip()]


def _comment(line: str) -> bool:
    return line.startswith("#")


def _fields(line: str) -> list[str]:
    marker = "@crapkit-record-v1\t"
    return json.loads(line[len(marker):]) if line.startswith(marker) else line.split("\t")


def _store_rows(sc, run_id: int) -> dict:
    found = sc.driver.store(
        "SELECT i.path, i.long_name, f.start, f.ccn, f.crap, f.cov FROM functions f "
        "JOIN identities i ON i.id = f.identity_id WHERE f.run_id = ?", (run_id,))
    return {(row["path"], row["long_name"]): (row["start"], row["ccn"], row["cov"], row["crap"])
            for row in found}


def _record_rows(rows: list[dict]) -> dict:
    return {(row["path"], row["long_name"]): (int(row["start"]), int(row["ccn"]), float(row["cov"]),
                                              float(row["crap"]))
            for row in rows}


@pytest.mark.process
@process
@given(decisions=st.integers(1, 9), covered=st.integers(0, 18))
def test_the_emitted_record_carries_the_store_s_rows_to_the_bit(repo_templates, tmp_path_factory,
                                                                 decisions, covered):
    """Any b2: the record's crap parses to the store's exact double, so a mark
    at four decimals compares the same against either."""
    fn = vw.Fn("b2", decisions, min(covered, 2 * decisions))
    world = WORLD.with_fn("lib", fn)
    sc = vw.Scenario(repo_templates.copy(vw.spec(world), tmp_path_factory.mktemp("rec") / "r"), world)
    assert sc.run("coverage").code == 0
    assert sc.run("verify", "--emit-baseline", "b.tsv", "--no-tighten").code == 0
    stamp, rows = read_record((sc.root / "b.tsv").read_bytes().decode("utf-8"))
    run = max(run.id for run in _runs(sc))
    assert _record_rows(rows) == _store_rows(sc, run)
    assert stamp.startswith(f"# commit={sc.head()}")
    crap = float(next(row["crap"] for row in rows if row["long_name"] == "b2( x )"))
    assert model.mark_value(crap) == model.mark_value(_store_rows(sc, run)[("lib/util.py", "b2( x )")][3])


# --- the verdict against the record -------------------------------------------------------------

def _emitted_clone(sc, tmp_path, name: str, columns: int = 17) -> vw.Scenario:
    """A copy of the repo with no store, holding the store baseline as b.tsv,
    cut to its first `columns` columns."""
    assert sc.run("verify", "--emit-baseline", "b.tsv", "--no-tighten").code == 0
    copy = sc.copy(tmp_path / name)
    shutil.rmtree(copy.root / ".crapkit")
    record = copy.root / "b.tsv"
    lines = record.read_bytes().decode("utf-8").split("\n")
    cut = [line if line.startswith("#") else "\t".join(line.split("\t")[:columns]) for line in lines]
    record.write_bytes("\n".join(cut).encode("utf-8"))
    return copy


def _verdict(result) -> tuple:
    payload = result.json()
    return (result.code, sorted((v["path"], v["long_name"]) for v in payload["gate_violations"]),
            sorted((r["path"], r["long_name"]) for r in payload["ratchet_regressions"]),
            sorted(payload["new_failures"]))


CHANGES = {"nothing": lambda w: w, "gate": lambda w: w.with_fn("app", WORSE),
           "new failure": lambda w: w.with_test(vw.Test("ta", failed=True)),
           "regression": lambda w: w.with_fn("lib", vw.Fn("b2", 7, 2))}


@pytest.mark.process
@pytest.mark.parametrize("columns", [17, 16])
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_the_record_and_the_store_give_one_verdict(make_repo, tmp_path, change, columns):
    """Seeded marks, then one change. verify against the store baseline and
    verify --baseline-tsv on a clone with no store, from the 17-column record
    and from its 16-column form (occurrence read as 0: no function here shares
    a start line), give one exit and one set of findings."""
    sc = _measured(make_repo)
    assert sc.run("ratchet", "seed").code == 0
    sc.commit("seed")
    copy = _emitted_clone(sc, tmp_path, f"clone-{columns}", columns)
    for scenario in (sc, copy):
        scenario.set(CHANGES[change](scenario.world))
    from_store = _verdict(sc.run("verify", "--no-tighten", "--json"))
    from_record = _verdict(copy.run("verify", "--baseline-tsv", "b.tsv", "--no-tighten", "--json"))
    assert from_record == from_store
    assert from_store[0] == {"nothing": 0, "gate": 6, "new failure": 8, "regression": 7}[change]


@pytest.mark.process
@rulings.applies("V8")
def test_a_failure_the_baseline_had_is_not_new_under_the_record(make_repo, tmp_path):
    """README exit 8: failures the baseline already had do not count. The
    store baseline forgives a test that failed at the baseline; the portable
    record of that same baseline does not."""
    world = WORLD.with_test(vw.Test("known", failed=True))
    sc = _measured(make_repo, world)
    store = sc.run("verify", "--no-tighten", "--json")
    copy = _emitted_clone(sc, tmp_path, "clone")
    record = copy.run("verify", "--baseline-tsv", "b.tsv", "--no-tighten", "--json")
    assert (store.code, store.json()["new_failures"]) == (0, [])
    said = f"exit {record.code}, new {','.join(record.json()['new_failures']) or 'none'}"
    rulings.pin_ruling("V8", crapkit=said, oracle="exit 0, new none")

