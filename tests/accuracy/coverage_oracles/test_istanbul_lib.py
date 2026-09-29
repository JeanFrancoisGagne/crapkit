"""istanbul's own library and crap-typescript, read over the same recordings crapkit reads.

1. istanbul-lib-coverage 3.2.2 totals every file's branches and statements. The
   counters no function holds (a branch arm by the function's span, a statement
   by its body, as docs/lanes.md reads them; outside_branches.mjs, json only) plus
   crapkit's per-function counts must equal those totals, covered and total
   compared as two separate numbers: a counter crapkit drops or counts twice
   moves one of them.
2. istanbul-lib's own uncovered lines part from crapkit's dark lines only on a
   line that holds a run statement and an unrun one (ruling D6).
3. crap-typescript 0.5.2 (the TypeScript compiler's method list, its own
   attribution) gives each TypeScript function the statement and branch
   percentages crapkit reads, except where a rulings row says why: it scores
   the smaller of the two (CO7), reads a default parameter as no branch (CO9),
   and folds an anonymous callback into its enclosing method (CO10). It reads an
   unrun arrow assigned to a const as unrun, as the ground truth does (CO-B1),
   and a function an ignore hint dropped as unknown, never another function's
   number.
4. Two functions on one line, the join's tie: istanbul-lib-instrument 6.0.3
   instruments a TypeScript line holding two arrows, and one is called with
   both arms of its ternary. crap-typescript matches each method to its fnMap
   entry by column and reads 2 of 2 and 0 of 2, as the calls say; crapkit
   reads spans by line and floors both to uncovered (ruling CO11).

All of it runs node, so it runs in the nightly tier.
"""
import json
from pathlib import Path

import pytest

import hang_guard
from accuracy.coverage_oracles import counts_table, mini_repo, probe_repo, under_test
from accuracy.kit import oracles, rulings, surfaces, tiers

pytestmark = [pytest.mark.nightly, pytest.mark.process]
HERE = Path(__file__).resolve().parent
RECORDED = probe_repo.RECORDED
ISTANBUL = under_test.crapkit("coverage_istanbul")
RECORDINGS = sorted(path for path in RECORDED.glob("*/*.json")
                    if "files" not in json.loads(path.read_bytes()))


def _node(script: str, *args: str) -> object:
    tiers.require_process("node")
    done = hang_guard.run(["node", str(HERE / script), str(oracles.node_modules("nightly")), *args],
                          text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


def _sums(functions: list) -> dict:
    return {"branches": (sum(fn.branches_total for fn in functions),
                         sum(fn.branches_covered for fn in functions)),
            "statements": (sum(fn.statements_total for fn in functions),
                           sum(fn.statements_covered for fn in functions))}


def _lib_side(entry: dict) -> dict:
    """istanbul-lib's totals less what lies outside every function."""
    return {kind: (entry[kind]["total"] - entry[f"outside{kind.title()}"]["total"],
                   entry[kind]["covered"] - entry[f"outside{kind.title()}"]["covered"])
            for kind in ("branches", "statements")}


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda path: f"{path.parent.name}-{path.name}")
def test_crapkit_sums_and_the_outside_equal_istanbul_lib_totals(path, oracle):
    oracle("istanbul-lib-coverage")
    lib = _node("outside_branches.mjs", str(path))
    per_file = ISTANBUL.parse_istanbul_both_file(path, repo_root="")[0]

    assert {key: _sums(per_file[key]) for key in lib} == {key: _lib_side(entry)
                                                         for key, entry in lib.items()}


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda path: f"{path.parent.name}-{path.name}")
def test_istanbul_lib_uncovered_lines_part_only_on_mixed_lines(path, oracle):
    oracle("istanbul-lib-coverage")
    lib = _node("outside_branches.mjs", str(path))
    dead = ISTANBUL.parse_istanbul_missing_file(path, repo_root="")
    artifact = json.loads(path.read_bytes())

    for key, entry in lib.items():
        theirs = set(entry["uncoveredLines"])
        assert (dead[key] - theirs, theirs - dead[key]) == (
            counts_table.mixed_lines(artifact[key]) & dead[key], set()), key


# --- crap-typescript ------------------------------------------------------------------------------

TYPESCRIPT = ("ts/shapes.ts", "tsx/shapes.tsx")
PRODUCERS = ("vitest-istanbul-5.0.1", "vitest-v8-5.0.1", "jest-babel-30.5.2")
# Where crap-typescript and crapkit read a function differently: the ruling that
# says why, by function name. An arrow assigned to a const is CO-B1, where
# crap-typescript sides with the ground truth.
PARTED = {"defaultParam": "CO9", "withCallback": "CO10", "arrow": "CO-B1", "h": "CO-B1"}


def crap_typescript(producer: str, scenario: str, probe: str) -> dict[int, dict]:
    """{start line: crap-typescript's method entry}."""
    artifact = RECORDED / producer / f"{scenario}.json"
    methods = _node("crap_typescript.mjs", str(artifact), str(probe_repo.PROBES),
                    str(probe_repo.PROBES / probe))
    return {method["start"]: method for method in methods}


def _ratio(covered: int, total: int) -> float | None:
    return round(covered / total, 9) if total else None


def _percent(metric: dict) -> float | None:
    return round(metric["percent"] / 100, 9) if metric["status"] == "measured" else None


def _crapkit_functions(producer: str, scenario: str, probe: str) -> dict[int, object]:
    artifact = RECORDED / producer / f"{scenario}.json"
    return {fn.start: fn for fn in ISTANBUL.parse_istanbul_both_file(artifact, repo_root="")[0]
            .get(probe, [])}


def _parts(fn, method: dict) -> list[str]:
    """How crapkit's statement and branch ratios differ from crap-typescript's."""
    ours = (_ratio(fn.statements_covered, fn.statements_total),
            _ratio(fn.branches_covered, fn.branches_total))
    theirs = (_percent(method["statements"]), _percent(method["branches"]))
    return [f"{method['name']} {kind}: crapkit {mine}, crap-typescript {other}"
            for kind, mine, other in zip(("statements", "branches"), ours, theirs)
            if other is not None and mine != other]


@pytest.mark.parametrize("probe", TYPESCRIPT)
@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
@pytest.mark.parametrize("producer", PRODUCERS)
def test_crap_typescript_reads_each_function_s_statements_and_branches(producer, scenario, probe,
                                                                       oracle):
    oracle("@barney-media/crap-typescript-core")
    ours = _crapkit_functions(producer, scenario, probe)
    methods = crap_typescript(producer, scenario, probe)

    assert [line for start, method in sorted(methods.items()) if start in ours
            and method["name"] not in PARTED for line in _parts(ours[start], method)] == []


def _absent(producer: str, scenario: str, probe: str) -> list[dict]:
    ours = _crapkit_functions(producer, scenario, probe)
    return [method for start, method in crap_typescript(producer, scenario, probe).items()
            if start not in ours]


@pytest.mark.parametrize("producer", PRODUCERS)
def test_crap_typescript_reads_a_dropped_function_as_unknown(producer, oracle):
    """The function an ignore hint dropped from the artifact: crap-typescript
    has nothing to attribute and says so (compare ruling CO-B5)."""
    oracle("@barney-media/crap-typescript-core")
    absent = _absent(producer, "call", "ts/shapes.ts")

    assert absent and {method["combined"]["status"] for method in absent} == {"unknown"}


def _pin(ruling: str, name: str, producer: str = "vitest-istanbul-5.0.1") -> None:
    ours = {fn.name.split("(")[0].strip(): fn for fn in
            _crapkit_functions(producer, "call", "ts/shapes.ts").values()}
    method = next(method for method in crap_typescript(producer, "call", "ts/shapes.ts").values()
                  if method["name"] == name)
    rulings.pin_ruling(ruling, crapkit=round(ours[name].coverage, 4),
                       oracle=round(method["combined"]["percent"] / 100, 4))


@rulings.applies("CO7")
def test_co7_crap_typescript_scores_the_smaller_of_statements_and_branches(oracle):
    """ignoredV8 under the istanbul provider: 3 of 4 branches, 2 of 3 statements."""
    oracle("@barney-media/crap-typescript-core")
    _pin("CO7", "ignoredV8")


@rulings.applies("CO9")
def test_co9_crap_typescript_reads_a_default_parameter_as_no_branch(oracle):
    oracle("@barney-media/crap-typescript-core")
    _pin("CO9", "defaultParam")


@rulings.applies("CO10")
def test_co10_crap_typescript_folds_a_callback_into_its_method(oracle):
    oracle("@barney-media/crap-typescript-core")
    _pin("CO10", "withCallback")


@rulings.applies("CO-B1")
def test_crap_typescript_reads_an_unrun_const_arrow_as_unrun(oracle):
    """`const arrow = (value) => value * 2;` never called: its declaration ran
    at import, and crap-typescript, attributing by position, leaves that
    statement outside the arrow."""
    oracle("@barney-media/crap-typescript-core")
    ours = {fn.start: fn for fn in _crapkit_functions("vitest-istanbul-5.0.1", "idle",
                                                      "ts/shapes.ts").values()}
    method = crap_typescript("vitest-istanbul-5.0.1", "idle", "ts/shapes.ts")[60]

    rulings.pin_ruling("CO-B1", crapkit=ours[60].coverage,
                       oracle=method["statements"]["percent"] / 100)


# --- two functions on one line: how each side breaks the tie ---------------------------------------

SIBLINGS = "export const called = (x) => (x ? 1 : 2), idle = (y) => (y ? 3 : 4);\n"
# The driver: called(true) and called(false) take both arms of called's ternary;
# nothing calls idle. Worked by hand: called 2 of 2 arms, idle 0 of 2.
SIBLING_CALLS = [["called", [True]], ["called", [False]]]
SIBLING_ARMS = {"called": 1.0, "idle": 0.0}


@pytest.fixture(scope="module")
def siblings(tmp_path_factory, oracle):
    """(crap-typescript's methods by name, crapkit's scored rows) over the
    coverage istanbul-lib-instrument records for SIBLINGS and SIBLING_CALLS.
    istanbul-lib-instrument comes with crap-typescript in the nightly Node tools
    (npm ci --prefix tools/accuracy/node/nightly), so a machine without them ends
    here as an infra miss, before node runs."""
    oracle("@barney-media/crap-typescript-core")
    work = tmp_path_factory.mktemp("siblings")
    source = work / "shared.ts"
    source.write_text(SIBLINGS, encoding="utf-8", newline="\n")
    coverage = _node("instrument_run.mjs", str(source), str(work), json.dumps(SIBLING_CALLS))
    artifact = work / "coverage.json"
    artifact.write_text(json.dumps(coverage), encoding="utf-8")
    methods = _node("crap_typescript.mjs", str(artifact), str(work), str(source))
    return ({method["name"]: method for method in methods},
            _sibling_rows(tmp_path_factory.mktemp("siblings-repo"), coverage))


def _sibling_rows(top: Path, coverage: dict) -> list[dict]:
    """crapkit's scored rows for SIBLINGS, its lane reading the same coverage."""
    entry = dict(next(iter(coverage.values())), path="ts/shared.ts")
    config = mini_repo.config([mini_repo.scope("ts", ["ts"], ["typescript"])],
                              [mini_repo.lane("ts", "istanbul", ["ts"])])
    driver = mini_repo.build(top / "repo", {"crapkit.toml": config, "ts/shared.ts": SIBLINGS,
                                            "recorded/ts.json": json.dumps({"ts/shared.ts": entry})})
    result = driver.run("coverage", "--export", "scored.tsv")
    assert result.code == 0, result.stderr
    return surfaces.read_tsv((driver.root / "scored.tsv").read_text(encoding="utf-8"))[1]


def test_crap_typescript_tells_two_functions_on_one_line_apart(siblings, oracle):
    oracle("@barney-media/crap-typescript-core")
    methods = siblings[0]

    assert {name: _percent(methods[name]["branches"]) for name in SIBLING_ARMS} == SIBLING_ARMS


def test_crapkit_floors_both_functions_on_one_line(siblings):
    """README.md#remedy-what-to-do-about-it: another function shares its source
    lines, so the score stays at uncovered whatever the tests do."""
    rows = siblings[1]

    assert sorted((int(row["start"]), float(row["cov"]), row["flag"]) for row in rows) == [
        (1, 0.0, "untested"), (1, 0.0, "untested")]


@rulings.applies("CO11")
def test_co11_crapkit_floors_what_crap_typescript_splits_by_column(siblings, oracle):
    oracle("@barney-media/crap-typescript-core")
    methods, rows = siblings
    called = next(row for row in rows if row["long_name"].startswith("called"))

    rulings.pin_ruling("CO11", crapkit=float(called["cov"]),
                       oracle=_percent(methods["called"]["combined"]))
