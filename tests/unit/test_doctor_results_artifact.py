"""A pytest or vitest lane with no `results_artifact` gets a doctor WARN (#26).

Two of verify's checks read the lane's junit: the crashed-worker trust check
("a run nobody finished is not a measurement") and no-new-failures (exit 8).
A lane that declares no results file never reaches either, and nothing said
so: doctor reported no problems for a configuration in which a dead xdist
worker still yielded a full, trusted baseline. WARN, never FAIL: the lane still
measures coverage exactly as it did.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import procs
from crapkit.cli import admin
from crapkit.cli.admin import _doctor_lanes
from crapkit.config import Lane


@pytest.fixture(autouse=True)
def probe_that_started(monkeypatch):
    """Every finding here is about the results file, none of it about whether
    this machine can start `python`. `_doctor_lanes` asks anyway, through
    `_lane_start_problem` -> `admin._start_probe`, which is an lru_cache keyed
    on the first word and nothing else, so any test in the process that shims a
    `python` onto PATH answers that word for every test after it. That is how
    three tests here failed in the combined lane run and passed alone: they read
    a FAIL naming a dead interpreter where the ok summary belongs.

    The answer is pinned to 0, the shell started it. Yields the real cached
    function so a test can fill it and prove it is not consulted, and clears it
    on both sides so this file neither reads nor leaves an answer."""
    real = admin._start_probe
    real.cache_clear()
    monkeypatch.setattr(admin, "_start_probe", lambda word: 0)
    yield real
    real.cache_clear()


def lane(name: str, parser: str, results_artifact: str = "") -> Lane:
    return Lane(name=name, command="python -m pytest", artifact=f".crapkit/cov/{name}.json",
                parser=parser, scopes=("src",), results_artifact=results_artifact)


def findings(*lanes: Lane, root: Path = Path(".")) -> list[tuple[str, str]]:
    cfg = SimpleNamespace(lanes=list(lanes), lane_less_scopes=[])
    return [(f.level, f.text) for f in _doctor_lanes(root, cfg)]


def test_a_pytest_lane_without_a_results_file_warns_and_names_what_is_off():
    (summary, warn) = findings(lane("py", "coveragepy"))

    assert summary == ("ok", "1 lane(s) declared")
    assert warn[0] == "WARN"
    assert warn[1].startswith("lane 'py' declares no results_artifact"), warn[1]
    assert "crashed-worker" in warn[1] and "exit 8" in warn[1], warn[1]


def test_the_pytest_hint_is_the_two_lines_that_fix_it():
    (_, warn) = findings(lane("py", "coveragepy"))

    assert "--junitxml=.crapkit/cov/junit-py.xml" in warn[1], warn[1]
    assert 'results_artifact = ".crapkit/cov/junit-py.xml"' in warn[1], warn[1]


def test_a_vitest_lane_gets_a_reporter_hint_instead():
    (_, warn) = findings(lane("js", "istanbul"))

    assert warn[1].startswith("lane 'js' declares no results_artifact"), warn[1]
    assert "junit" in warn[1] and "--junitxml" not in warn[1], warn[1]


def test_a_lane_that_declares_one_is_left_alone():
    assert findings(lane("py", "coveragepy", ".crapkit/cov/junit-py.xml")) == \
        [("ok", "1 lane(s) declared")]


def test_a_probe_answer_another_test_cached_never_reaches_these_findings(
        probe_that_started, monkeypatch):
    """The isolation the three assertions above depend on, asserted once.

    tests/unit/test_init_probe.py puts a `python` on PATH that exits 9009 and
    asks the probe about it. Before both files cleared the cache, that answer
    outlived its test and `findings()` reported a lane whose interpreter cannot
    start, for a lane running the same python this suite runs under.

    Fills the real cache the same way and reads the ok summary through it."""
    monkeypatch.setattr(procs, "run_bounded", lambda command, timeout: 9009)
    assert probe_that_started("python") == 9009, "the poison has to land"

    assert findings(lane("py", "coveragepy", "junit-py.xml")) == \
        [("ok", "1 lane(s) declared")]


def test_one_line_per_lane_missing_it():
    levels = [level for level, _ in findings(lane("a", "coveragepy"), lane("b", "istanbul"),
                                             lane("c", "coveragepy", "c.xml"))]

    assert levels == ["ok", "WARN", "WARN"]
