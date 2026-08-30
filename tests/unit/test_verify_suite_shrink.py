"""The suite-size warning `verify` prints from lane provenance.

A pass/fail check cannot see a suite that quietly shrank, so `_warn_suite_shrink`
compares this run's junit counts with the baseline's and says so on stderr. The
counts are optional on both sides: a lane with no `results_artifact` writes none,
and a baseline recorded before the lane declared one carries none.
"""
from crapkit.cli.verifying import _warn_suite_shrink


def warnings(baseline: dict, provenance: dict, capsys) -> list[str]:
    _warn_suite_shrink(baseline, provenance)
    return capsys.readouterr().err.splitlines()


def test_a_shrunken_suite_is_named_with_the_count(capsys):
    lines = warnings({"lanes": {"py": {"tests_total": 100, "tests_skipped": 2}}},
                     {"py": {"tests_total": 90, "tests_skipped": 2}}, capsys)

    assert lines == ["warning: lane 'py' runs 10 fewer tests than the baseline"]


def test_more_skips_are_named_with_the_count(capsys):
    lines = warnings({"lanes": {"py": {"tests_total": 100, "tests_skipped": 2}}},
                     {"py": {"tests_total": 100, "tests_skipped": 7}}, capsys)

    assert lines == ["warning: lane 'py' skips 5 more tests than the baseline"]


def test_an_unchanged_suite_is_silent(capsys):
    counts = {"tests_total": 100, "tests_skipped": 2}

    assert warnings({"lanes": {"py": counts}}, {"py": dict(counts)}, capsys) == []


def test_a_baseline_without_counts_compares_nothing(capsys):
    """A baseline recorded before the lane declared a results_artifact."""
    assert warnings({"lanes": {"py": {}}}, {"py": {"tests_total": 5}}, capsys) == []


def test_a_lane_that_wrote_no_counts_says_so_instead_of_crashing(capsys):
    """The baseline was measured with junit and this run's lane wrote none: an
    older commit gated against a newer baseline, or a results_artifact that went
    missing. 0.4.4 read the absent count as 0 for the comparison, then indexed
    it for the message, and the KeyError landed after the whole lane had run.
    """
    lines = warnings({"lanes": {"py": {"tests_total": 3785, "tests_skipped": 5}}},
                     {"py": {}}, capsys)

    assert len(lines) == 1, lines
    assert "no test counts" in lines[0] and "3785" in lines[0], lines[0]
    assert "fewer" not in lines[0]
