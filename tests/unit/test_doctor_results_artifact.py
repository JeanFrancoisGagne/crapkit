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

from crapkit.cli.admin import _doctor_lanes
from crapkit.config import Lane


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


def test_one_line_per_lane_missing_it():
    levels = [level for level, _ in findings(lane("a", "coveragepy"), lane("b", "istanbul"),
                                             lane("c", "coveragepy", "c.xml"))]

    assert levels == ["ok", "WARN", "WARN"]
