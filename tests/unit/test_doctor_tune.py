"""The knob suggestions behind `crapkit doctor --tune`, and the junit duration
read that gives them a cost signal.

The three parallelism knobs moved a weekly run from 136 to 56 minutes and were
hand-derived every time. These are pure arithmetic on a cpu count and whatever
lane durations already sit on disk: advisory, and identical for identical input.
"""
from crapkit.doctor import parallel_seconds, suggest_knobs, tune_lines
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
    from crapkit.cli import _lane_durations
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
    from crapkit.cli import _lane_durations
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
