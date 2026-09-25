"""doctor --tune: the knob suggestions and the lane-cost estimate.

Oracles: a brute-force optimal makespan for up to 8 lanes and Graham's LPT
bound (oracles/makespan.py), junitparser's reading of a JUnit report for the
fallback duration, and docs/configuration.md:386-408 and docs/lanes.md
:1242-1286 through model_score.
"""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import re

from hypothesis import given, strategies as st
import junitparser
import pytest

from accuracy.kit import drive, repos, rulings
from accuracy.kit.settings import pure
from accuracy.score_model import cases, cli_repo, model_score, production
from accuracy.score_model.oracles import makespan

TUNE_ROWS = cases.hand("doctor --tune knobs and lane cost")
SEED_JUNIT = Path(__file__).resolve().parents[1] / "kit" / "fixtures" / "seed" / "recorded" / "py-junit.xml"


def knobs(cpus: int, lanes: int, shared: tuple = ()):
    return production.load("doctor:suggest_knobs")(cpus=cpus, lanes=lanes, shared=shared)


def lpt_seconds(durations, slots: int) -> float:
    return production.load("doctor:parallel_seconds")(tuple(durations), slots)


def _numbers(text: str) -> list[float]:
    return [float(part) for part in text.split(",")]


@pytest.mark.parametrize("given_,expected", [row[1:] for row in TUNE_ROWS if "cpus" in row[1]],
                         ids=[row[0] for row in TUNE_ROWS if "cpus" in row[1]])
def test_knob_hand_rows(given_, expected):
    got = knobs(int(given_["cpus"]), int(given_["lanes"]))

    assert {key: str(getattr(got, key)) for key in expected} == expected


@pytest.mark.parametrize("given_,expected", [row[1:] for row in TUNE_ROWS if "durations" in row[1]],
                         ids=[row[0] for row in TUNE_ROWS if "durations" in row[1]])
def test_cost_hand_rows(given_, expected):
    durations, slots = _numbers(given_["durations"]), int(given_["slots"])

    assert lpt_seconds(durations, slots) == float(expected["lpt"])
    assert makespan.optimal(durations, slots) == float(expected["optimal"])


def test_lpt_is_a_bound_not_the_optimum():
    """D11: doctor.parallel_seconds says LPT is exact for a real config's few
    lanes; (3, 3, 2, 2, 2) on 2 slots gives 7 where 6 is reachable."""
    rulings.pin_ruling("D11", crapkit=lpt_seconds((3, 3, 2, 2, 2), 2),
                       oracle=makespan.optimal([3, 3, 2, 2, 2], 2))


@given(st.lists(st.integers(1, 60), min_size=1, max_size=8), st.integers(1, 4))
@pure
def test_the_cost_line_is_lpt_within_graham_s_bound(seconds, slots):
    got = lpt_seconds([float(s) for s in seconds], slots)
    best = makespan.optimal(seconds, slots)

    assert got == makespan.lpt(seconds, slots)
    assert best <= got <= makespan.graham_bound(slots) * best


@given(st.integers(1, 128), st.integers(0, 20), st.booleans())
@pure
def test_the_suggestion_never_passes_the_lanes_and_holds_shared_data_at_one(cpus, lanes, shared):
    got = knobs(cpus, lanes, (("a", "b"),) if shared else ())

    assert model_score.lane_slots_ok(got.max_parallel_lanes, lanes, shared)
    assert got.analysis_workers >= 1 and got.mutation_workers >= 1


@given(st.integers(1, 127), st.integers(1, 20))
@pure
def test_more_cpus_never_suggest_fewer_workers(cpus, lanes):
    fewer, more = knobs(cpus, lanes), knobs(cpus + 1, lanes)

    assert more.max_parallel_lanes >= fewer.max_parallel_lanes
    assert more.analysis_workers >= fewer.analysis_workers
    assert more.mutation_workers >= fewer.mutation_workers


def test_knob_formulas_beyond_the_worked_example():
    """SM-TUNE-KNOBS: the docs show 24 cpus only; at 8, crapkit keeps one core
    for the shell and gives a quarter of the box to mutation workers."""
    got = knobs(8, 4)

    rulings.pin_ruling("SM-TUNE-KNOBS", crapkit=f"{got.analysis_workers},{got.mutation_workers}",
                       oracle="unspecified")


# --- the JUnit fallback ----------------------------------------------------------------------

def _suites(text: str) -> list:
    report = junitparser.JUnitXml.fromstring(text)
    return [report] if isinstance(report, junitparser.TestSuite) else list(report)


def _junitparser_seconds(text: str) -> float:
    """Suite totals, else the cases summed, as junitparser reads them."""
    suites = _suites(text)
    total = sum(suite.time or 0.0 for suite in suites)
    return total or sum(_case_seconds(suite) for suite in suites)


def _case_seconds(suite) -> float:
    return sum(case.time or 0.0 for case in suite)


def test_junitparser_is_the_pinned_oracle(oracle):
    assert oracle("junitparser").version == junitparser.version


def test_the_seed_junit_costs_what_junitparser_reads():
    text = SEED_JUNIT.read_text(encoding="utf-8")

    assert production.load("junitparse:suite_seconds")(text) == pytest.approx(
        _junitparser_seconds(text), abs=1e-9)


def _report(suites: list[tuple[float | None, list[float]]]) -> str:
    parts = []
    for n, (suite_time, cases_) in enumerate(suites):
        stamp = "" if suite_time is None else f' time="{suite_time}"'
        body = "".join(f'<testcase classname="c" name="t{k}" time="{t}"/>' for k, t in enumerate(cases_))
        parts.append(f'<testsuite name="s{n}" tests="{len(cases_)}"{stamp}>{body}</testsuite>')
    return f"<testsuites>{''.join(parts)}</testsuites>"


@given(st.lists(st.tuples(st.one_of(st.none(), st.integers(1, 9000).map(lambda n: n / 100)),
                          st.lists(st.integers(0, 900).map(lambda n: n / 100), min_size=1, max_size=5)),
                min_size=1, max_size=4))
@pure
def test_suite_seconds_matches_junitparser(suites):
    """Suite totals first, else the cases summed (junitparse.suite_seconds docstring,
    docs/lanes.md: the wall time the junit report claims)."""
    if any(time is None for time, _ in suites):
        suites = [(None, cases_) for _, cases_ in suites]
    text = _report(suites)

    assert production.load("junitparse:suite_seconds")(text) == pytest.approx(
        _junitparser_seconds(text), abs=1e-9)


# --- shared coverage data holds the slots at one ----------------------------------------------------

def _lane(name: str, cwd: str, coverage_file: str | None) -> str:
    env = {"COVERAGE_PROCESS_CONFIG": "", "COV_CORE_DATAFILE": ""}
    if coverage_file:
        env["COVERAGE_FILE"] = coverage_file
    pairs = ", ".join(f'{key} = "{value}"' for key, value in env.items())
    return (f'[[lane]]\nname = "{name}"\ncommand = "python -c pass"\nartifact = ".crapkit/cov/{name}.json"\n'
            f'parser = "coveragepy"\nscopes = ["a"]\ncwd = "{cwd}"\nenv = {{ {pairs} }}\n')


def _tune_config(lanes: list[tuple[str, str, str | None]]) -> str:
    head = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "a"\npaths = ["a"]\nlanguages = ["python"]\n\n'
    return head + "\n".join(_lane(*lane) for lane in lanes)


FILES = st.sampled_from([None, ".coverage", ".coverage.b", ".coverage.b.c", "x/.coverage", ".cov"])


@given(st.lists(st.tuples(st.sampled_from([".", "sub"]), FILES), min_size=1, max_size=4))
@pure
def test_shared_data_groups_follow_the_lanes_doc(lanes):
    named = [(f"l{n}", cwd, file) for n, (cwd, file) in enumerate(lanes)]
    cfg = production.load("config:load_config_text")(_tune_config(named))
    groups = production.load("doctor:shared_coverage_data")(cfg.lanes)

    assert bool(groups) == _any_collide([_data_file(cwd, file) for _, cwd, file in named])


def _data_file(cwd: str, file: str | None) -> str:
    return model_score.coverage_data_file(cwd, {"COVERAGE_FILE": file} if file else {})


def _any_collide(files: list[str]) -> bool:
    pairs = ((a, b) for n, a in enumerate(files) for b in files[n + 1:])
    return any(model_score.data_files_collide(a, b) for a, b in pairs)


@given(st.sampled_from([".crapkit/cov/py.json", ".crapkit/old/py.json", "moved.json"]),
       st.lists(st.integers(1, 900), min_size=1, max_size=3))
@pure
def test_a_lane_s_duration_survives_its_artifact_moving(where, tenths):
    """The longest stamp recorded under the lane's name costs the lane when no
    stamp sits at its declared artifact (docs/configuration.md:386-388: the
    durations already recorded)."""
    cfg = production.load("config:load_config_text")(_tune_config([("py", ".", None)]))
    stamps = {f"{where}.{n}": {"commit": "0" * 40, "lane": "py", "seconds": t / 10}
              for n, t in enumerate(tenths)}

    assert production.load("lanes:recorded_seconds")(stamps, cfg.lanes[0]) == max(tenths) / 10


def _doctor_tune(make_repo, lanes, stamps: dict | None = None) -> str:
    built = make_repo(repos.Spec(steps=(repos.Commit(files={
        "crapkit.toml": _tune_config(lanes), "a/m.py": "def f(x):\n    return x\n"}),)))
    if stamps is not None:
        (built.root / ".crapkit").mkdir(exist_ok=True)
        (built.root / ".crapkit" / "artifacts.json").write_text(json.dumps(stamps), encoding="utf-8")
    result = drive.Driver(built.root).run("doctor", "--tune")
    assert result.code == 0, result.stderr
    return result.stdout


@pytest.mark.nightly
@pytest.mark.process
def test_fix_24ccf00(make_repo):
    """R113: a lane's recorded duration still costs it after its artifact moved:
    the stamp names the lane, and the cost line reads it."""
    stamps = {".crapkit/cov/old-place.json": {"commit": "0" * 40, "lane": "py", "seconds": 3.5}}
    out = _doctor_tune(make_repo, [("py", ".", None)], stamps)

    assert "# lane cost: 3.5s serial -> ~3.5s across 1 lane slot(s)" in out


@pytest.mark.nightly
@pytest.mark.process
def test_fix_d8ae57a(make_repo):
    """R114: two coveragepy lanes on one .coverage are held at one slot, named."""
    out = _doctor_tune(make_repo, [("one", ".", None), ("two", ".", None)])

    assert "max_parallel_lanes = 1" in out and "# held at 1: lanes 'one', 'two'" in out


@pytest.mark.nightly
@pytest.mark.process
def test_fix_a6d8ce7(make_repo):
    """R115: a lane left on .coverage deletes and combines a sibling's .coverage.b."""
    out = _doctor_tune(make_repo, [("base", ".", None), ("side", ".", ".coverage.b")])

    assert "# held at 1: lanes 'base', 'side'" in out


RESOURCES_LINE = re.compile(r"resources: up to (\d+) analysis worker\(s\) per pool, (\d+) shared "
                            r"slot\(s\); lane log limit (\d+) bytes per file")


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.cross_surface
def test_doctor_text_json_and_check_config_print_one_resource_policy(make_repo):
    """doctor's resources line, `doctor --json` resources and check_config's
    structuredContent resources name the same pool limits."""
    cli = cli_repo.driver(make_repo, cli_repo.SURFACES)
    printed = RESOURCES_LINE.search(cli.run("doctor").stdout)
    as_json = cli.json("doctor")["resources"]
    (mcp,) = cli.mcp([("check_config", {})])

    assert printed is not None
    assert [int(value) for value in printed.groups()] == [
        as_json["pool_worker_limit"], as_json["shared_pool_limit"], as_json["log_max_bytes"]]
    assert mcp["structuredContent"]["resources"] == as_json
