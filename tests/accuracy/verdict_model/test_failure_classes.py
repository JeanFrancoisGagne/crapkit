"""Test failure classes: new, forgiven and retried-pass failures, and the dirty subset.

The rules come from CONTEXT.md (Forgiven failure, Flake retry, Retried pass),
docs/lanes.md#flake-retest and agent-json.md (Findings, Dirty attribution),
restated in model_verdict.failures and model_verdict.dirty_failures:

- new = what the fresh run failed minus what the baseline failed, a
  baseline's retried passes excluded from the latter;
- forgiven = failed by both;
- a new failure drops out of new_failures, into retried_passes, only when
  every lane that failed it declares a retest_command and its rerun passed;
- dirty_failures = the new failures whose test file has uncommitted edits.

Each case sets a baseline run and a fresh verify through the CLI and compares
verify's JSON with the model. The property test draws the whole shape.
"""
from __future__ import annotations

from dataclasses import replace
from xml.etree import ElementTree

from hypothesis import given, strategies as st
import pytest

from accuracy.kit.settings import process
from accuracy.verdict_model import cadence
from accuracy.verdict_model import model_verdict as model
from accuracy.verdict_model import verdict_world as vw

BASE = (vw.World().with_fn("app", vw.Fn("f", 1, 2)).with_fn("lib", vw.Fn("g", 1, 2))
        .with_test(vw.Test("keep_a")).with_test(vw.Test("keep_b", lane="b")))
SHARED = "tests.shared"


def failing_tests(failing: dict) -> tuple:
    """failing: {(lane, name, module): failed?}"""
    return tuple(vw.Test(name, failed, lane, module) for (lane, name, module), failed in failing.items())


def world(failing: dict, retest_lanes=frozenset(), retest_pass=frozenset()) -> vw.World:
    return replace(BASE, tests=BASE.tests + failing_tests(failing), retest_lanes=frozenset(retest_lanes),
                   retest_pass=frozenset(retest_pass))


def _lanes_failing(state: vw.World) -> dict:
    out: dict = {}
    for test in state.tests:
        if test.failed:
            out.setdefault(test.id, set()).add(test.lane)
    return out


def _passed_retry(state: vw.World, failing: dict) -> dict:
    return {test: {lane for lane in lanes if lane in state.retest_lanes}
            for test, lanes in failing.items() if test in state.retest_pass}


def expected(base: vw.World, fresh: vw.World) -> model.Failures:
    failing = _lanes_failing(fresh)
    return model.failures(failing, set(_lanes_failing(base)), set(), _passed_retry(fresh, failing))


def got(payload: dict) -> model.Failures:
    return model.Failures(*(tuple(sorted(payload[key])) for key in
                            ("new_failures", "forgiven_failures", "retried_passes")))


@pytest.fixture(scope="module")
def clean(seeded_world):
    return seeded_world(BASE)


def verify_after(clean, tmp, base: vw.World, fresh: vw.World) -> dict:
    """A coverage run on `base` becomes the baseline; verify `fresh` against it."""
    scenario = clean.copy(tmp)
    scenario.set(base)
    assert scenario.run("coverage").code == 0
    scenario.set(fresh)
    result = scenario.run("verify", "--json")
    assert result.code == (8 if expected(base, fresh).new else 0), result.stdout + result.stderr
    return result.json()


# --- hand cases ------------------------------------------------------------------------------

@pytest.mark.process
def test_forgiven_and_new_failures_split_by_the_baseline(clean, tmp_path):
    base = world({("a", "old", ""): True})
    fresh = world({("a", "old", ""): True, ("a", "fresh", ""): True})

    assert got(verify_after(clean, tmp_path / "r", base, fresh)) == model.Failures(
        ("tests.test_a::fresh",), ("tests.test_a::old",), ())


@pytest.mark.process
def test_ok_line_names_forgiven_failures(clean, tmp_path):
    """CONTEXT.md, Forgiven failure: it fails no verdict, and the OK line
    counts it: `(N unchanged failures forgiven, first ID)` (agent-json.md)."""
    scenario = clean.copy(tmp_path / "r")
    scenario.set(world({("a", "old1", ""): True, ("a", "old2", ""): True}))
    assert scenario.run("coverage").code == 0

    result = scenario.run("verify")

    assert result.code == 0, result.stdout + result.stderr
    assert "(2 unchanged failures forgiven, first tests.test_a::old1)" in result.stdout


@pytest.mark.process
@pytest.mark.parametrize("retests, new", cadence.tiered(
    [((), ("tests.shared::both",)), (("a",), ("tests.shared::both",)), (("a", "b"), ())],
    push={"one-lane"}, ids=["no-retest", "one-lane", "both-lanes"], unpack=True))
def test_two_lanes_failing_one_id(clean, tmp_path, retests, new):
    """lanes.md#flake-retest: a test several lanes failed drops out only when
    each of those lanes reran it and it passed."""
    fresh = world({("a", "both", SHARED): True, ("b", "both", SHARED): True}, retests,
                  {"tests.shared::both"})

    result = got(verify_after(clean, tmp_path / "r", BASE, fresh))

    assert result == expected(BASE, fresh)
    assert result.new == new


@pytest.mark.nightly
@pytest.mark.process
def test_retried_pass_never_forgives_a_later_failure(clean, tmp_path):
    """CONTEXT.md, Retried pass: a later verify never forgives it as a baseline
    failure. The first verify passes on the retry and becomes the baseline;
    the same test failing again is new."""
    scenario = clean.copy(tmp_path / "r")
    scenario.set(world({("a", "flaky", ""): True}, {"a"}, {"tests.test_a::flaky"}))
    first = scenario.run("verify", "--json")
    assert (first.code, first.json()["retried_passes"]) == (0, ["tests.test_a::flaky"])

    scenario.set(world({("a", "flaky", ""): True}))
    second = scenario.run("verify", "--json")

    assert (second.code, second.json()["new_failures"], second.json()["forgiven_failures"]) == \
        (8, ["tests.test_a::flaky"], [])


@pytest.mark.nightly
@pytest.mark.process
def test_a_retried_pass_is_not_forgiven(clean, tmp_path):
    """The baseline verify's retried pass is left out of the baseline's
    failures, so it is never counted forgiven."""
    scenario = clean.copy(tmp_path / "r")
    scenario.set(world({("a", "flaky", ""): True}, {"a"}, {"tests.test_a::flaky"}))
    assert scenario.run("verify").code == 0
    scenario.set(world({("a", "flaky", ""): True}, {"a"}, {"tests.test_a::flaky"}))

    payload = scenario.run("verify", "--json").json()

    assert (payload["forgiven_failures"], payload["retried_passes"]) == ([], ["tests.test_a::flaky"])


@pytest.mark.process
@pytest.mark.parametrize("retried", cadence.tiered([True, False], push={"retried"},
                                                   ids=["retried", "not-retried"]))
def test_dirty_failures_drop_a_retried_pass(clean, tmp_path, retried):
    """agent-json.md, Dirty attribution: dirty_failures is the subset of
    new_failures whose test file has uncommitted edits; a retried pass is not
    a new failure, so it is not dirty either."""
    passing = {"tests.test_a::flaky"} if retried else set()
    fresh = world({("a", "flaky", ""): True, ("a", "real", ""): True}, {"a"}, passing)
    scenario = clean.copy(tmp_path / "r")
    scenario.set(fresh)
    (scenario.root / "tests" / "test_a.py").write_text("# edited, not committed\n", encoding="utf-8")

    payload = scenario.run("verify", "--json").json()

    new = sorted(expected(BASE, fresh).new)
    assert (payload["new_failures"], payload["dirty_failures"]) == \
        (new, model.dirty_failures(new, {"tests/test_a.py"}))


# --- the world's own rerun ----------------------------------------------------------------------

def _rerun_results(fresh: vw.World, lane: str, root) -> dict:
    """{test id: failed?} in the JUnit the world's retest_command writes for `lane`."""
    vw.rerun(vw.plan(fresh), lane, root)
    cases = ElementTree.parse(root / ".crapkit" / "cov" / f"{lane}-junit.xml").iter("testcase")
    return {f"{case.get('classname')}::{case.get('name')}": case.find("failure") is not None
            for case in cases}


def test_the_world_s_rerun_passes_only_the_listed_ids(tmp_path):
    """A rerun passes a failed test only when its id, classname::name, is in
    retest_pass: a test of the same name in another module still fails."""
    fresh = world({("b", "t2", ""): True, ("b", "t2", SHARED): True}, {"b"}, {"tests.shared::t2"})

    assert _rerun_results(fresh, "b", tmp_path) == {"tests.test_b::t2": True, "tests.shared::t2": False}


def test_the_world_keeps_one_test_name_in_two_modules():
    """with_test replaces the test of the same id, not every test of that name."""
    both = BASE.with_test(vw.Test("t2", lane="b", module=SHARED)).with_test(vw.Test("t2", lane="b"))

    assert [test.id for test in both.tests if test.name == "t2"] == ["tests.shared::t2", "tests.test_b::t2"]


@pytest.mark.nightly
@pytest.mark.process
def test_a_rerun_pass_in_one_module_leaves_the_same_name_new(clean, tmp_path):
    """The shape the property test drew on Windows and Linux (Windows seed
    25453015425846708203250584874946972796): lane b fails tests.test_b::t2 and
    only tests.shared::t2 passes a rerun, so tests.test_b::t2 stays new and
    verify exits 8 (lanes.md#flake-retest: a failure drops out only when its
    own rerun passed)."""
    fresh = world({("b", "t2", ""): True, ("b", "t2", SHARED): False}, {"a", "b"}, {"tests.shared::t2"})

    assert got(verify_after(clean, tmp_path / "r", BASE, fresh)) == model.Failures(
        ("tests.test_b::t2",), (), ())


# --- property -----------------------------------------------------------------------------------

NAMES = st.sampled_from(["t1", "t2", "t3"])
KEYS = st.tuples(st.sampled_from(["a", "b"]), NAMES, st.sampled_from(["", SHARED]))


@pytest.mark.nightly
@pytest.mark.process
@process
@given(base=st.dictionaries(KEYS, st.booleans(), max_size=4),
       fresh=st.dictionaries(KEYS, st.booleans(), max_size=4),
       retests=st.sets(st.sampled_from(["a", "b"])),
       passing=st.sets(st.sampled_from([f"{m}::{n}" for m in ("tests.test_a", "tests.test_b", SHARED)
                                        for n in ("t1", "t2", "t3")])))
def test_failure_classes_follow_the_model(tmp_path_factory, clean, base, fresh, retests, passing):
    base_world, fresh_world = world(base), world(fresh, retests, passing)

    payload = verify_after(clean, tmp_path_factory.mktemp("classes") / "r", base_world, fresh_world)

    assert got(payload) == expected(base_world, fresh_world)
