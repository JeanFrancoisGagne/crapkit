"""Parallel-lane scheduling, at the two seams that decide it.

`lane_order` picks what STARTS first (wall time only). `_collect_lanes` decides
what the run SCORES, and it must not care which lane finished first: same tree,
same artifacts, same numbers, serial or not.
"""
from crapkit.cli import _collect_lanes
from crapkit.config import Lane, load_config_text
from crapkit.lanes import LaneOutcome, lane_order, write_stamps

BASE = '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def _lane(name: str) -> Lane:
    return Lane(name=name, command="", artifact=f"{name}.json", parser="istanbul", scopes=("src",))


LANES = [_lane("unit"), _lane("ui"), _lane("py")]


def test_the_slowest_recorded_lane_starts_first(tmp_path):
    write_stamps(tmp_path, {"unit.json": {"commit": "a", "lane": "unit", "seconds": 10.0},
                            "ui.json": {"commit": "a", "lane": "ui", "seconds": 300.0},
                            "py.json": {"commit": "a", "lane": "py", "seconds": 60.0}})
    assert [l.name for l in lane_order(tmp_path, LANES)] == ["ui", "py", "unit"]


def test_lanes_that_never_recorded_a_duration_keep_declaration_order(tmp_path):
    assert [l.name for l in lane_order(tmp_path, LANES)] == ["unit", "ui", "py"]


def test_a_tie_breaks_on_declaration_order(tmp_path):
    write_stamps(tmp_path, {"unit.json": {"commit": "a", "lane": "unit", "seconds": 5.0},
                            "py.json": {"commit": "a", "lane": "py", "seconds": 5.0}})
    assert [l.name for l in lane_order(tmp_path, LANES)] == ["unit", "py", "ui"]


def test_a_hand_edited_duration_does_not_crash_the_schedule(tmp_path):
    write_stamps(tmp_path, {"ui.json": {"commit": "a", "lane": "ui", "seconds": "ages"}})
    assert [l.name for l in lane_order(tmp_path, LANES)] == ["unit", "ui", "py"]


def _moved(name: str, artifact: str) -> Lane:
    return Lane(name=name, command="", artifact=artifact, parser="istanbul", scopes=("src",))


def test_a_renamed_artifact_path_does_not_orphan_the_lanes_duration(tmp_path):
    """Stamps are filed under the artifact path, so moving an artifact used to
    zero the lane out of the schedule. the consumer repo moved 12 of them and 11 of its
    14 lanes sorted as never-measured, which makes longest-first do nothing."""
    write_stamps(tmp_path, {"coverage/coverage-final.json":
                            {"commit": "a", "lane": "unit", "seconds": 3194.4}})
    declared = [_lane("ui"), _lane("py"), _moved("unit", ".crapkit/cov/unit/coverage-final.json")]
    assert [l.name for l in lane_order(tmp_path, declared)] == ["unit", "ui", "py"]


def test_the_artifact_a_lane_declares_outranks_an_older_record_of_the_same_lane(tmp_path):
    """The declared path is the exact record. A duration filed under a path the
    lane no longer writes is a fallback, never an override."""
    write_stamps(tmp_path, {"new/unit.json": {"commit": "a", "lane": "unit", "seconds": 1.0},
                            "coverage/coverage-final.json":
                            {"commit": "a", "lane": "unit", "seconds": 3194.4},
                            "ui.json": {"commit": "a", "lane": "ui", "seconds": 29.0}})
    declared = [_moved("unit", "new/unit.json"), _lane("ui"), _lane("py")]
    assert [l.name for l in lane_order(tmp_path, declared)] == ["ui", "unit", "py"]


def test_a_lane_no_stamp_ever_named_is_still_unmeasured(tmp_path):
    write_stamps(tmp_path, {"other.json": {"commit": "a", "lane": "someone-else", "seconds": 900.0}})
    assert [l.name for l in lane_order(tmp_path, LANES)] == ["unit", "ui", "py"]


def _outcome(marker: str) -> LaneOutcome:
    return LaneOutcome({"src/a.ts": [marker]}, {"scopes": ["src"]}, {})


def test_results_merge_in_declaration_order_whatever_order_they_finished(tmp_path):
    unit, ui, py = LANES
    finished_last_first = {py: (_outcome("py"), ""), ui: (_outcome("ui"), ""),
                           unit: (_outcome("unit"), "")}
    coverage, provenance, errors, succeeded = _collect_lanes(tmp_path, LANES, finished_last_first)
    assert coverage["src/a.ts"] == ["unit", "ui", "py"]
    assert list(provenance) == ["unit", "ui", "py"]
    assert [l.name for l in succeeded] == ["unit", "ui", "py"]
    assert errors == {}


def test_a_failed_lane_is_recorded_and_skipped_not_fatal(tmp_path, capsys):
    unit, ui, py = LANES
    outcomes = {unit: (_outcome("unit"), ""), ui: (None, "no artifact"),
                py: (_outcome("py"), "")}
    coverage, provenance, errors, succeeded = _collect_lanes(tmp_path, LANES, outcomes)
    assert coverage["src/a.ts"] == ["unit", "py"]
    assert errors == {"ui": "no artifact"}
    assert "lane 'ui' FAILED" in capsys.readouterr().err


def test_two_lanes_sharing_a_name_are_still_two_lanes(tmp_path):
    """The config forces ARTIFACTS to be unique, not names. Keying the outcome
    map on the name would run one of these and count it twice."""
    twins = [Lane(name="unit", command="", artifact=f"{side}.json", parser="istanbul",
                  scopes=("src",)) for side in ("a", "b")]
    outcomes = {twins[0]: (_outcome("a"), ""), twins[1]: (_outcome("b"), "")}
    coverage, _, _, succeeded = _collect_lanes(tmp_path, twins, outcomes)
    assert coverage["src/a.ts"] == ["a", "b"]
    assert len(succeeded) == 2


def test_max_parallel_lanes_defaults_to_serial():
    assert load_config_text(BASE).max_parallel_lanes == 1
    assert load_config_text(BASE + "[crapkit]\nmax_parallel_lanes = 3\n").max_parallel_lanes == 3


def test_analysis_workers_defaults_to_the_pools_own_default():
    assert load_config_text(BASE).analysis_workers == 0
    assert load_config_text(BASE + "[crapkit]\nanalysis_workers = 12\n").analysis_workers == 12
