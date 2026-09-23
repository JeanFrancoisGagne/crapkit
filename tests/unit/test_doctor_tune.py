"""The knob suggestions behind `crapkit doctor --tune`, and the junit duration
read that gives them a cost signal.

The three parallelism knobs moved a weekly run from 136 to 56 minutes and were
hand-derived every time. These are pure arithmetic on a cpu count and whatever
lane durations already sit on disk: advisory, and identical for identical input.
"""
from crapkit.config import Lane
from crapkit.doctor import parallel_seconds, shared_coverage_data, suggest_knobs, tune_lines
from crapkit.junitparse import suite_seconds


def test_knobs_leave_the_box_room_to_breathe():
    knobs = suggest_knobs(cpus=16, lanes=3)
    assert knobs.analysis_workers == 15, "one core stays for the shell watching the run"
    assert knobs.mutation_workers == 4, "a mutation worker holds a whole suite: a quarter of 16"
    assert knobs.max_parallel_lanes == 3, "3 lanes, and 16 cpus can afford 4 slots"


def test_more_lanes_than_slots_are_capped_by_the_cpu_count():
    assert suggest_knobs(cpus=4, lanes=5).max_parallel_lanes == 1
    assert suggest_knobs(cpus=8, lanes=5).max_parallel_lanes == 2


def test_a_one_core_box_never_suggests_zero_of_anything():
    knobs = suggest_knobs(cpus=1, lanes=2)
    assert (knobs.max_parallel_lanes, knobs.analysis_workers, knobs.mutation_workers) == (1, 1, 1)


def test_a_repo_with_no_lanes_still_gets_one_slot():
    assert suggest_knobs(cpus=32, lanes=0).max_parallel_lanes == 1


def test_the_makespan_is_the_busiest_slot_not_the_average():
    # 100 + 60 + 40 over two slots: the 100 lane runs alone, 60 and 40 pair up.
    assert parallel_seconds((100.0, 60.0, 40.0), 2) == 100.0
    assert parallel_seconds((100.0, 60.0, 40.0), 3) == 100.0
    assert parallel_seconds((100.0, 60.0, 40.0), 1) == 200.0


def test_no_durations_is_a_zero_makespan_not_a_crash():
    assert parallel_seconds((), 4) == 0.0


def test_the_cost_line_quotes_both_ends_of_the_trade():
    lines = tune_lines(cpus=16, knobs=suggest_knobs(cpus=16, lanes=3),
                       durations=(100.0, 60.0, 40.0))
    assert lines[0] == "# doctor --tune: suggestions for 16 cpu(s); nothing was written"
    assert lines[1] == "[crapkit]"
    assert lines[2:5] == ["max_parallel_lanes = 3", "analysis_workers = 15",
                          "mutation_workers = 4"]
    assert lines[5] == "# lane cost: 200.0s serial -> ~100.0s across 3 lane slot(s)"


def test_without_a_cost_signal_the_suggestion_says_so():
    lines = tune_lines(cpus=8, knobs=suggest_knobs(cpus=8, lanes=2), durations=())
    assert lines[-1] == ("# lane cost: no durations recorded yet — "
                         "suggested from the cpu count alone")


def test_lane_durations_prefer_the_recorded_run_and_fall_back_to_junit(tmp_path):
    """artifacts.json holds a duration only for lanes that actually ran here; a
    lane whose artifact was reused still has its junit report to cost it."""
    from crapkit.cli.admin import _lane_durations
    from crapkit.config import load_config_text
    from crapkit.lanes import write_stamps

    write_stamps(tmp_path, {"cov/a.json": {"commit": "abc", "lane": "a", "seconds": 12.5}})
    (tmp_path / "b-results.xml").write_text('<testsuite time="7.5"/>', encoding="utf-8")
    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
        '[[lane]]\nname = "a"\ncommand = "x"\nartifact = "cov/a.json"\n'
        'parser = "istanbul"\nscopes = ["src"]\n\n'
        '[[lane]]\nname = "b"\ncommand = "y"\nartifact = "cov/b.json"\n'
        'parser = "istanbul"\nscopes = ["src"]\nresults_artifact = "b-results.xml"\n')

    assert _lane_durations(tmp_path, cfg) == (12.5, 7.5)


def test_a_lane_with_no_signal_at_all_contributes_no_duration(tmp_path):
    from crapkit.cli.admin import _lane_durations
    from crapkit.config import load_config_text

    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
        '[[lane]]\nname = "a"\ncommand = "x"\nartifact = "cov/a.json"\n'
        'parser = "istanbul"\nscopes = ["src"]\n')
    assert _lane_durations(tmp_path, cfg) == ()


def test_junit_reports_its_own_wall_seconds():
    xml = ('<testsuites><testsuite name="a" time="2.5"/>'
           '<testsuite name="b" time="3.0"/></testsuites>')
    assert suite_seconds(xml) == 5.5


def test_a_suite_with_no_time_falls_back_to_its_testcases():
    xml = ('<testsuite name="a">'
           '<testcase classname="t" name="x" time="0.25"/>'
           '<testcase classname="t" name="y" time="0.75"/></testsuite>')
    assert suite_seconds(xml) == 1.0


def test_a_report_with_no_timing_at_all_is_zero_seconds():
    assert suite_seconds('<testsuite><testcase classname="t" name="x"/></testsuite>') == 0.0
    assert suite_seconds('<testsuite time="nope"><testcase name="x"/></testsuite>') == 0.0


def _lane(name: str, *, parser: str = "coveragepy", cwd: str = "", env=(),
          command: str = "python -m pytest --cov --cov-report=json") -> Lane:
    return Lane(name, command, f".crapkit/cov/{name}.json", parser, ("src",), cwd=cwd, env=env)


def test_coveragepy_lanes_started_in_one_directory_write_one_data_file():
    """Both write .coverage where they start; run at once, one dies with
    `sqlite3.OperationalError: table coverage_schema already exists`."""
    lanes = [_lane("a"), _lane("b"), _lane("ts", parser="istanbul"), _lane("c", cwd="pkg")]
    assert shared_coverage_data(lanes) == (("a", "b"),)


def _file(name: str) -> tuple[tuple[str, str], ...]:
    return (("COVERAGE_FILE", name),)


def test_lanes_that_each_name_their_own_data_file_share_none():
    assert shared_coverage_data([_lane("a", env=_file(".coverage.a")),
                                 _lane("b", env=_file(".coverage.b"))]) == ()
    assert shared_coverage_data([_lane("a", cwd="pkg_a"), _lane("b", cwd="pkg_b")]) == ()
    assert shared_coverage_data([
        _lane("a", command="coverage run --data-file=.coverage.a -m pytest"),
        _lane("b", env=_file(".coverage.b"))]) == ()


def test_one_coverage_file_named_in_both_lanes_is_still_shared():
    env = _file(".coverage.x")
    assert shared_coverage_data([_lane("a", env=env), _lane("b", env=env)]) == (("a", "b"),)


def test_a_lane_on_the_default_file_shares_with_siblings_named_after_it():
    """pytest-cov deletes `.coverage` and every `.coverage.*` beside it when a
    lane starts, and combines `.coverage.*` when it ends, so a lane left on the
    default takes in the file and the in-flight pieces of a lane on
    `.coverage.b`. Run at once, one of them failed with WinError 32 on
    `.coverage.b.<host>.pid<N>.<rand>` and the run came back partial."""
    assert shared_coverage_data([_lane("a"), _lane("b", env=_file(".coverage.b"))]) == (
        ("a", "b"),)
    assert shared_coverage_data([_lane("b", env=_file(".coverage.b")), _lane("a"),
                                 _lane("c", env=_file(".coverage.c"))]) == (("b", "a", "c"),)
    assert shared_coverage_data([
        _lane("a", command="coverage run --data-file .coverage.a -m pytest"), _lane("b")]) == (
        ("a", "b"),)


def test_a_dotted_name_in_another_directory_is_not_combined():
    assert shared_coverage_data([_lane("a"), _lane("b", env=_file("sub/.coverage.b"))]) == ()
    assert shared_coverage_data([_lane("a"), _lane("b", env=_file(".coverage.d/b"))]) == ()
    assert shared_coverage_data([_lane("a", env=_file(".coverage.a")),
                                 _lane("b", env=_file(".coverage.ab"))]) == ()


def test_lanes_sharing_a_data_file_hold_the_lane_slots_at_one():
    knobs = suggest_knobs(cpus=16, lanes=2, shared=(("a", "b"),))
    assert knobs.max_parallel_lanes == 1
    lines = tune_lines(cpus=16, knobs=knobs, durations=())
    assert lines[2:4] == [
        "max_parallel_lanes = 1",
        "# held at 1: lanes 'a', 'b' write coverage.py data files that one of them deletes "
        "and combines, and two of them at once can fail one lane; give each lane its own "
        "COVERAGE_FILE, for example env = { COVERAGE_FILE = \".coverage.a\" } in lane 'a' and "
        "env = { COVERAGE_FILE = \".coverage.b\" } in lane 'b', then rerun doctor --tune"]
