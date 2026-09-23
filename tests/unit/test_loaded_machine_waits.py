"""A wait that only guards against a hung child survives a loaded machine.

Verify run 103 failed six tests on a correct tree, all waits that hit their own
bound while the machine was saturated: three tests of
test_measurement_inputs_e2e.py hit its 30 s CLI bound, a mutation run allowed its
two-file suite 5 s, an inline ready wait allowed 10 s and a helper 15 s. Each of
those numbers was a guess about how slow a child can be. The hang bound replaces
the guess; these tests hold the suite to it. A lane or mutation deadline that no
test is about bounds a child the same way, so it is held to the bound too.

The loaded machine here is a clock that moves only when a wait sleeps, one
second per poll, so no test measures wall-clock time.
"""
import ast
from pathlib import Path
import re
import time

import pytest

import mutation_fixtures
import state_concurrency_worker
import test_ci_driver_lifetime
import test_r2_execution_lifetime
from hang_guard import HANG_SECONDS

TESTS = Path(__file__).resolve().parents[1]
LATE = 60  # past every bound run 103 tripped on, well inside the hang bound


@pytest.fixture
def loaded_machine(tmp_path, monkeypatch):
    """A marker the child writes LATE seconds in, on a clock only sleeps advance."""
    marker = tmp_path / "started"
    now = [0.0]

    def sleep(_seconds):
        now[0] += 1
        if now[0] >= LATE:
            marker.touch()

    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(time, "sleep", sleep)
    return marker


SHARED_WAITS = [test_r2_execution_lifetime, mutation_fixtures, state_concurrency_worker,
                test_ci_driver_lifetime]


@pytest.mark.parametrize("helper", SHARED_WAITS, ids=lambda module: module.__name__)
def test_a_marker_a_loaded_machine_writes_late_is_still_seen(loaded_machine, helper):
    helper.wait_for(loaded_machine)

    assert loaded_machine.exists()


def _is_cli_runner(node):
    return isinstance(node, ast.Call) and getattr(node.func, "id", None) == "cli_runner"


def _spelled_timeouts(call):
    return [keyword.value.value for keyword in call.keywords
            if keyword.arg == "timeout" and isinstance(keyword.value, ast.Constant)]


def _cli_runner_bounds(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [bound for node in ast.walk(tree) if _is_cli_runner(node)
            for bound in _spelled_timeouts(node)]


def test_no_e2e_file_binds_the_cli_below_the_hang_bound():
    tight = {path.name: bound for path in sorted((TESTS / "e2e").glob("*.py"))
             for bound in _cli_runner_bounds(path) if bound < HANG_SECONDS}

    assert tight == {}, "an e2e CLI run bounded under the hang bound fails on a loaded machine"


MUTATION_DEADLINE = re.compile(r"""mutation_timeout_seconds["']?\s*[:=]\s*(\d+)""")

# A deadline a test is about keeps its number: (file, seconds) -> the reason.
UNDER_TEST = {
    ("unit/test_lanes_infra.py", 1):
        "test_lane_timeout_kills_the_command_and_says_so expects this lane to time out",
    ("unit/test_holding_suite_raises_the_mutation_deadline.py", 10):
        "the deadline holding_suite must raise to the hold",
}


def _relative(path):
    return path.relative_to(TESTS).as_posix()


def _tight(sites):
    """The (file, seconds) deadlines under the hang bound that no test is about.
    0 is no deadline: the product then sets no timeout at all."""
    return [site for site in sites if 0 < site[1] < HANG_SECONDS and site not in UNDER_TEST]


def _mutation_deadlines(path):
    text = path.read_text(encoding="utf-8")
    return [(_relative(path), int(found.group(1))) for found in MUTATION_DEADLINE.finditer(text)]


def test_a_mutation_run_that_is_not_about_its_deadline_waits_the_hang_bound():
    sites = [site for path in sorted(TESTS.rglob("*.py")) for site in _mutation_deadlines(path)]

    assert _tight(sites) == [], "a suite that must finish is bounded by the hang bound, not a guess"


def _called(call):
    return {getattr(call.func, "id", None), getattr(call.func, "attr", None)}


def _is_lane(node):
    return isinstance(node, ast.Call) and "Lane" in _called(node)


def _spelled_timeout(keyword):
    return keyword.arg == "timeout_seconds" and isinstance(keyword.value, ast.Constant)


def _lane_timeouts(path):
    lanes = filter(_is_lane, ast.walk(ast.parse(path.read_text(encoding="utf-8"))))
    return [(_relative(path), keyword.value.value) for lane in lanes
            for keyword in filter(_spelled_timeout, lane.keywords)]


def test_a_lane_that_is_not_about_its_timeout_waits_the_hang_bound():
    sites = [site for path in sorted(TESTS.rglob("*.py")) for site in _lane_timeouts(path)]

    assert _tight(sites) == [], "a lane a test runs to its end is bounded by the hang bound"
